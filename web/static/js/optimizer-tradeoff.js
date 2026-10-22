/**
 * Pareto scatter and stored-trajectory charts.
 * Plots only the series the page embedded. A null value stays a gap.
 */
(function () {
    if (typeof Plotly === "undefined" || typeof regolithChartLayout !== "function") {
        return;
    }

    function readJson(id) {
        var node = document.getElementById(id);
        if (!node || !node.textContent) {
            return null;
        }
        try {
            return JSON.parse(node.textContent);
        } catch (error) {
            return null;
        }
    }

    function plotPareto(payload) {
        var node = document.getElementById("pareto-scatter");
        if (!payload || !node) {
            return;
        }
        var theme = regolithChartTheme();
        var buckets = {
            feasible: { x: [], y: [], text: [], customdata: [], size: [], width: [] },
            infeasible: { x: [], y: [], text: [], customdata: [], size: [], width: [] },
            unavailable: { x: [], y: [], text: [], customdata: [], size: [], width: [] },
        };
        (payload.points || []).forEach(function (point) {
            if (point.plotted === false || point.x === null || point.x === undefined) {
                return;
            }
            if (point.y === null || point.y === undefined) {
                return;
            }
            var status = buckets[point.plot_status] ? point.plot_status : "unavailable";
            var bucket = buckets[status];
            bucket.x.push(point.x);
            bucket.y.push(point.y);
            bucket.text.push(point.label || point.candidate_id || point.cache_key);
            bucket.customdata.push(point.detail_url || "");
            bucket.size.push(point.on_pareto_front ? 16 : 9);
            bucket.width.push(point.on_pareto_front ? 2 : 0);
        });
        var style = {
            feasible: { symbol: "circle", color: theme.accent },
            infeasible: { symbol: "x", color: theme.danger },
            unavailable: { symbol: "diamond", color: theme.warning },
        };
        var traces = [];
        ["feasible", "infeasible", "unavailable"].forEach(function (status) {
            var bucket = buckets[status];
            if (!bucket.x.length) {
                return;
            }
            traces.push({
                type: "scatter",
                mode: "markers",
                name: status,
                x: bucket.x,
                y: bucket.y,
                text: bucket.text,
                customdata: bucket.customdata,
                hovertemplate: "%{text}<extra></extra>",
                marker: {
                    symbol: style[status].symbol,
                    color: style[status].color,
                    size: bucket.size,
                    line: { width: bucket.width, color: theme.text },
                },
            });
        });
        if (!traces.length) {
            return;
        }
        Plotly.newPlot(node, traces, regolithChartLayout({
            xaxis: { title: payload.x_label || payload.x || "" },
            yaxis: { title: payload.y_label || payload.y || "" },
        }), window.regolithChartConfig);
        node.on("plotly_click", function (event) {
            var point = event.points && event.points[0];
            if (point && point.customdata) {
                window.location.assign(point.customdata);
            }
        });
    }

    function plotLines(elementId, points, yTitle) {
        var node = document.getElementById(elementId);
        if (!node || !points || !points.length) {
            return;
        }
        var theme = regolithChartTheme();
        var styles = [
            { dash: "solid", symbol: "circle", color: theme.accent },
            { dash: "dash", symbol: "square", color: theme.primary },
            { dash: "dot", symbol: "diamond", color: theme.warning },
            { dash: "dashdot", symbol: "triangle-up", color: theme.danger },
        ];
        var traces = [];
        points.forEach(function (point, index) {
            var rows = point.series_points;
            if (!rows) {
                return;
            }
            var style = styles[index % styles.length];
            traces.push({
                type: "scatter",
                mode: "lines+markers",
                name: point.label || point.candidate_id || point.cache_key || "",
                x: rows.map(function (row) { return row.hour; }),
                y: rows.map(function (row) {
                    return row.value === undefined ? null : row.value;
                }),
                connectgaps: false,
                line: { color: style.color, dash: style.dash, width: 2 },
                marker: { symbol: style.symbol, color: style.color, size: 7 },
            });
        });
        if (!traces.length) {
            return;
        }
        Plotly.newPlot(node, traces, regolithChartLayout({
            xaxis: { title: "Hour" },
            yaxis: { title: yTitle || "" },
        }), window.regolithChartConfig);
    }

    plotPareto(readJson("pareto-payload"));

    var trajectory = readJson("trajectory-payload");
    if (trajectory) {
        plotLines("optimizer-trajectory-chart", [{
            label: trajectory.series_label,
            series_points: trajectory.series_points,
        }], trajectory.y_title || trajectory.series_label);
    }

    var compare = readJson("compare-payload");
    if (compare && compare.chart) {
        plotLines(
            "compare-chart",
            (compare.points || []).filter(function (point) { return point.series_points; }),
            compare.series_label
        );
    }
}());
