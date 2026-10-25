# Sanbongi and Omori (1959) cell audit

## Method and row reconciliation

The scanned PDF has no text layer. The two machine reads were MinerU table blocks and the independent Tesseract OCR; the Tesseract table layouts run cells together or misread digits on these pages, and the pdftotext layout file is empty. I checked the table layout and every stored numeric cell below against the corresponding page render, so each cell is marked (b). The page number is the absolute PDF page; the printed page is given separately.

The rendered table row counts are Table 1: 12; Table 2: 11; Table 3: 52 (26 on pages 10 and 11); Table 4: 7. Total: 82 physical rows. Tables 1–3 yield 12 activities, 11 activities plus 11 coefficients, and 52 activities plus 52 coefficients. Table 4 yields six scalar activity observations and one typed refusal for its printed interval. Thus the extract stores 144 scalar observations and one interval context record.

Cell columns below are printed table columns. Table 1 stores c2–c5 compositions and c7 activity; c6 e.m.f. is omitted as a schema gap. Table 2 stores c2–c5 compositions, c6 CaO/SiO2 mole ratio, c8 activity, and c9 coefficient; c7 e.m.f. is a schema gap. Table 3 stores c2–c7 compositions, c8 CaO/SiO2 mole ratio, c10 activity, and c11 coefficient; c9 e.m.f. is a schema gap. Table 4 stores c2 activity, c3 temperature, and c4 publication year. No printed e.m.f. value has a registered quantity token.

## Stored cells by printed row

| PDF page | Printed page | Table | Printed row | Stored source cells and assigned values | Check |
|---:|---:|---:|---|---|---|
| 5 | 248 | 1 | NB-1 | `c2=55.39`; `c3=44.61`; `c4=54.60`; `c5=45.40`; `c7=0.008`; `caption T=1300°C` | (b) render p.5 |
| 5 | 248 | 1 | NB-2 | `c2=52.81`; `c3=47.29`; `c4=51.96`; `c5=48.04`; `c7=0.014`; `caption T=1300°C` | (b) render p.5 |
| 5 | 248 | 1 | NB-3 | `c2=49.46`; `c3=50.54`; `c4=48.66`; `c5=51.34`; `c7=0.04`; `caption T=1300°C` | (b) render p.5 |
| 5 | 248 | 1 | NB-4 | `c2=46.00`; `c3=54.00`; `c4=45.21`; `c5=54.79`; `c7=0.08`; `caption T=1300°C` | (b) render p.5 |
| 5 | 248 | 1 | NB-5 | `c2=41.78`; `c3=58.22`; `c4=41.01`; `c5=58.99`; `c7=0.15`; `caption T=1300°C` | (b) render p.5 |
| 5 | 248 | 1 | NB-6 | `c2=38.55`; `c3=61.45`; `c4=37.80`; `c5=62.20`; `c7=0.30`; `caption T=1300°C` | (b) render p.5 |
| 5 | 248 | 1 | NB-7 | `c2=34.61`; `c3=65.39`; `c4=33.80`; `c5=66.18`; `c7=0.49`; `caption T=1300°C` | (b) render p.5 |
| 5 | 248 | 1 | NB-8 | `c2=32.73`; `c3=67.27`; `c4=32.03`; `c5=67.97`; `c7=0.50`; `caption T=1300°C` | (b) render p.5 |
| 5 | 248 | 1 | NB-9 | `c2=30.47`; `c3=69.53`; `c4=29.80`; `c5=70.20`; `c7=0.68`; `caption T=1300°C` | (b) render p.5 |
| 5 | 248 | 1 | NB-10 | `c2=24.46`; `c3=75.54`; `c4=23.88`; `c5=76.12`; `c7=0.82`; `caption T=1300°C` | (b) render p.5 |
| 5 | 248 | 1 | NB-11 | `c2=20.32`; `c3=79.68`; `c4=19.81`; `c5=80.19`; `c7=0.97`; `caption T=1300°C` | (b) render p.5 |
| 5 | 248 | 1 | NB-12 | `c2=16.80`; `c3=83.20`; `c4=16.36`; `c5=83.64`; `c7=1.00`; `caption T=1300°C` | (b) render p.5 |
| 9 | 252 | 2 | CB- 1 | `c2=57.9`; `c3=42.4`; `c4=59.4`; `c5=40.6`; `c6=1.46`; `c8=0.04`; `c9=0.099`; `caption T=1630°C` | (b) render p.9 |
| 9 | 252 | 2 | CB- 2 | `c2=54.9`; `c3=44.8`; `c4=56.8`; `c5=43.2`; `c6=1.31`; `c8=0.08`; `c9=0.185`; `caption T=1630°C` | (b) render p.9 |
| 9 | 252 | 2 | CB- 3 | `c2=51.8`; `c3=48.5`; `c4=55.4`; `c5=46.6`; `c6=1.19`; `c8=0.14`; `c9=0.300`; `caption T=1630°C` | (b) render p.9 |
| 9 | 252 | 2 | CB- 4 | `c2=48.3`; `c3=51.0`; `c4=50.4`; `c5=49.6`; `c6=1.02`; `c8=0.20`; `c9=0.403`; `caption T=1630°C` | (b) render p.9 |
| 9 | 252 | 2 | CB- 5 | `c2=46.0`; `c3=53.8`; `c4=47.8`; `c5=52.2`; `c6=0.92`; `c8=0.23`; `c9=0.441`; `caption T=1630°C` | (b) render p.9 |
| 9 | 252 | 2 | CB- 6 | `c2=42.8`; `c3=57.2`; `c4=44.5`; `c5=55.5`; `c6=0.80`; `c8=0.40`; `c9=0.721`; `caption T=1630°C` | (b) render p.9 |
| 9 | 252 | 2 | CB- 7 | `c2=32.0`; `c3=59.5`; `c4=41.8`; `c5=58.2`; `c6=0.72`; `c8=0.60`; `c9=1.031`; `caption T=1630°C` | (b) render p.9 |
| 9 | 252 | 2 | CB- 8 | `c2=36.8`; `c3=64.0`; `c4=38.2`; `c5=61.8`; `c6=0.62`; `c8=0.73`; `c9=1.181`; `caption T=1630°C` | (b) render p.9 |
| 9 | 252 | 2 | CB- 9 | `c2=35.1`; `c3=64.8`; `c4=36.8`; `c5=63.2`; `c6=0.58`; `c8=0.83`; `c9=1.313`; `caption T=1630°C` | (b) render p.9 |
| 9 | 252 | 2 | CB-10 | `c2=33.3`; `c3=66.8`; `c4=34.8`; `c5=65.2`; `c6=0.53`; `c8=0.90`; `c9=1.380`; `caption T=1630°C` | (b) render p.9 |
| 9 | 252 | 2 | CB-11 | `c2=32.3`; `c3=69.4`; `c4=31.8`; `c5=68.2`; `c6=0.47`; `c8=0.99`; `c9=1.452`; `caption T=1630°C` | (b) render p.9 |
| 10 | 253 | 3 | T 5-1 | `c2=50`; `c3=45`; `c4=5`; `c5=52.8`; `c6=44.3`; `c7=2.9`; `c8=1.19`; `c10=0.08`; `c11=0.180`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T 5-2 | `c2=47`; `c3=48`; `c4=5`; `c5=49.7`; `c6=47.4`; `c7=2.9`; `c8=1.05`; `c10=0.13`; `c11=0.274`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T 5-3 | `c2=45`; `c3=50`; `c4=5`; `c5=47.7`; `c6=49.4`; `c7=2.9`; `c8=0.97`; `c10=0.18`; `c11=0.364`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T 5-4 | `c2=40`; `c3=55`; `c4=5`; `c5=42.5`; `c6=54.6`; `c7=2.9`; `c8=0.78`; `c10=0.35`; `c11=0.641`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T 5-5 | `c2=35`; `c3=60`; `c4=5`; `c5=32.4`; `c6=59.7`; `c7=2.9`; `c8=0.63`; `c10=0.54`; `c11=0.910`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T 5-6 | `c2=30`; `c3=65`; `c4=5`; `c5=32.1`; `c6=65.0`; `c7=2.9`; `c8=0.49`; `c10=0.55`; `c11=0.846`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T 5-7 | `c2=25`; `c3=70`; `c4=5`; `c5=26.9`; `c6=70.2`; `c7=2.9`; `c8=0.38`; `c10=0.70`; `c11=0.997`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T 5-8 | `c2=20`; `c3=75`; `c4=5`; `c5=21.6`; `c6=74.5`; `c7=3.0`; `c8=0.29`; `c10=0.90`; `c11=1.208`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T10-1 | `c2=50`; `c3=40`; `c4=10`; `c5=53.8`; `c6=40.2`; `c7=5.9`; `c8=1.33`; `c10=0.08`; `c11=0.199`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T10-2 | `c2=45`; `c3=45`; `c4=10`; `c5=48.6`; `c6=45.4`; `c7=5.9`; `c8=1.07`; `c10=0.14`; `c11=0.308`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T10-3 | `c2=40`; `c3=50`; `c4=10`; `c5=43.4`; `c6=50.6`; `c7=6.0`; `c8=0.86`; `c10=0.35`; `c11=0.692`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T10-4 | `c2=35`; `c3=55`; `c4=10`; `c5=38.1`; `c6=55.9`; `c7=6.0`; `c8=0.68`; `c10=0.56`; `c11=1.002`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T10-5 | `c2=30`; `c3=60`; `c4=10`; `c5=32.8`; `c6=61.2`; `c7=6.0`; `c8=0.54`; `c10=0.78`; `c11=1.275`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T10-6 | `c2=25`; `c3=65`; `c4=10`; `c5=27.5`; `c6=66.5`; `c7=6.0`; `c8=0.41`; `c10=0.84`; `c11=1.263`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T10-7 | `c2=20`; `c3=70`; `c4=10`; `c5=22.0`; `c6=71.9`; `c7=6.1`; `c8=0.31`; `c10=0.87`; `c11=1.210`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T10-8 | `c2=15`; `c3=75`; `c4=10`; `c5=16.6`; `c6=77.4`; `c7=6.0`; `c8=0.21`; `c10=0.94`; `c11=1.214`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T10-9 | `c2=10`; `c3=80`; `c4=10`; `c5=11.1`; `c6=82.8`; `c7=6.1`; `c8=0.13`; `c10=1.00`; `c11=1.208`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T15-1 | `c2=45`; `c3=40`; `c4=15`; `c5=49.7`; `c6=41.2`; `c7=9.1`; `c8=1.20`; `c10=0.08`; `c11=0.194`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T15-2 | `c2=40`; `c3=45`; `c4=15`; `c5=44.3`; `c6=46.6`; `c7=9.1`; `c8=0.95`; `c10=0.14`; `c11=0.300`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T15-3 | `c2=35`; `c3=50`; `c4=15`; `c5=38.9`; `c6=51.9`; `c7=9.2`; `c8=0.75`; `c10=0.33`; `c11=0.636`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T15-4 | `c2=30`; `c3=55`; `c4=15`; `c5=33.7`; `c6=57.3`; `c7=9.2`; `c8=0.59`; `c10=0.57`; `c11=0.995`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T15-5 | `c2=25`; `c3=60`; `c4=15`; `c5=28.0`; `c6=62.7`; `c7=9.3`; `c8=0.46`; `c10=0.65`; `c11=1.037`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T15-6 | `c2=20`; `c3=65`; `c4=15`; `c5=22.5`; `c6=68.2`; `c7=9.3`; `c8=0.33`; `c10=0.78`; `c11=1.143`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T15-7 | `c2=15`; `c3=70`; `c4=15`; `c5=16.9`; `c6=73.8`; `c7=9.3`; `c8=0.23`; `c10=0.83`; `c11=1.125`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T15-8 | `c2=10`; `c3=75`; `c4=15`; `c5=11.3`; `c6=79.3`; `c7=9.4`; `c8=0.14`; `c10=0.80`; `c11=1.009`; `caption T=1630°C` | (b) render p.10 |
| 10 | 253 | 3 | T20-1 | `c2=40`; `c3=40`; `c4=20`; `c5=45.3`; `c6=42.3`; `c7=12.4`; `c8=1.07`; `c10=0.10`; `c11=0.236`; `caption T=1630°C` | (b) render p.10 |
| 11 | 254 | 3 continued | T20-2 | `c2=35`; `c3=45`; `c4=20`; `c5=39.8`; `c6=49.4`; `c7=12.5`; `c8=0.83`; `c10=0.20`; `c11=0.419`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T20-3 | `c2=30`; `c3=50`; `c4=20`; `c5=34.2`; `c6=53.2`; `c7=12.6`; `c8=0.64`; `c10=0.36`; `c11=0.677`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T20-4 | `c2=25`; `c3=55`; `c4=20`; `c5=28.6`; `c6=58.8`; `c7=12.6`; `c8=0.49`; `c10=0.39`; `c11=0.663`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T20-5 | `c2=20`; `c3=60`; `c4=20`; `c5=23.0`; `c6=64.4`; `c7=12.6`; `c8=0.36`; `c10=0.46`; `c11=0.714`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T20-6 | `c2=15`; `c3=65`; `c4=20`; `c5=17.3`; `c6=70.0`; `c7=12.7`; `c8=0.25`; `c10=0.65`; `c11=0.929`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T20-7 | `c2=10`; `c3=70`; `c4=20`; `c5=11.6`; `c6=75.7`; `c7=12.7`; `c8=0.12`; `c10=0.70`; `c11=0.925`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T25-1 | `c2=45`; `c3=30`; `c4=25`; `c5=51.9`; `c6=32.3`; `c7=15.8`; `c8=1.60`; `c10=0.037`; `c11=0.115`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T25-2 | `c2=40`; `c3=35`; `c4=25`; `c5=46.3`; `c6=37.8`; `c7=15.9`; `c8=1.22`; `c10=0.08`; `c11=0.212`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T25-3 | `c2=35`; `c3=40`; `c4=25`; `c5=40.6`; `c6=43.4`; `c7=16.0`; `c8=0.94`; `c10=0.16`; `c11=0.369`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T25-4 | `c2=30`; `c3=45`; `c4=25`; `c5=35.0`; `c6=49.0`; `c7=16.0`; `c8=0.71`; `c10=0.26`; `c11=0.531`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T25-5 | `c2=25`; `c3=50`; `c4=25`; `c5=29.3`; `c6=54.6`; `c7=16.1`; `c8=0.54`; `c10=0.36`; `c11=0.659`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T25-6 | `c2=20`; `c3=55`; `c4=25`; `c5=23.5`; `c6=60.3`; `c7=16.2`; `c8=0.39`; `c10=0.35`; `c11=0.580`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T25-7 | `c2=15`; `c3=60`; `c4=25`; `c5=17.7`; `c6=61.9`; `c7=16.2`; `c8=0.27`; `c10=0.47`; `c11=0.711`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T25-8 | `c2=10`; `c3=65`; `c4=25`; `c5=11.8`; `c6=71.9`; `c7=16.3`; `c8=0.10`; `c10=0.58`; `c11=0.801`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T30-1 | `c2=50`; `c3=20`; `c4=30`; `c5=58.7`; `c6=21.9`; `c7=19.4`; `c8=2.68`; `c10=0.015`; `c11=0.068`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T30-2 | `c2=40`; `c3=30`; `c4=30`; `c5=47.4`; `c6=33.1`; `c7=19.5`; `c8=1.43`; `c10=0.04`; `c11=0.121`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T30-3 | `c2=35`; `c3=35`; `c4=30`; `c5=41.6`; `c6=38.8`; `c7=19.6`; `c8=1.07`; `c10=0.07`; `c11=0.180`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T30-4 | `c2=30`; `c3=40`; `c4=30`; `c5=35.8`; `c6=44.5`; `c7=19.7`; `c8=0.81`; `c10=0.16`; `c11=0.360`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T30-5 | `c2=25`; `c3=45`; `c4=30`; `c5=29.9`; `c6=50.3`; `c7=19.8`; `c8=0.59`; `c10=0.23`; `c11=0.457`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T30-6 | `c2=20`; `c3=50`; `c4=30`; `c5=24.1`; `c6=56.1`; `c7=19.8`; `c8=0.43`; `c10=0.22`; `c11=0.392`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | T30-7 | `c2=15`; `c3=55`; `c4=30`; `c5=18.1`; `c6=62.0`; `c7=19.9`; `c8=0.29`; `c10=0.31`; `c11=0.500`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | AS-1 | `c2=5.7`; `c3=77.9`; `c4=15.2`; `c5=6.6`; `c6=83.8`; `c7=9.6`; `c8=0.079`; `c10=0.83`; `c11=0.990`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | AS-2 | `c2=12.3`; `c3=56.5`; `c4=32.1`; `c5=14.8`; `c6=63.8`; `c7=21.4`; `c8=0.23`; `c10=0.38`; `c11=0.596`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | AS-3 | `c2=18.3`; `c3=50.8`; `c4=30.9`; `c5=22.1`; `c6=57.3`; `c7=20.6`; `c8=0.38`; `c10=0.33`; `c11=0.576`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | AS-4 | `c2=20.8`; `c3=31.8`; `c4=47.0`; `c5=27.5`; `c6=38.3`; `c7=34.2`; `c8=0.72`; `c10=0.12`; `c11=0.313`; `caption T=1630°C` | (b) render p.11 |
| 11 | 254 | 3 continued | AS-5 | `c2=30.3`; `c3=18.1`; `c4=51.0`; `c5=40.2`; `c6=22.5`; `c7=37.3`; `c8=1.79`; `c10=0.05`; `c11=0.222`; `caption T=1630°C` | (b) render p.11 |
| 18 | 261 | 4 | Present work | `c2=0.03`; `c3=1630`; `c4=1957` | (b) render p.18 |
| 18 | 261 | 4 | $\text{Rey}^{(9)}$ | `c2=0.10`; `c3=1600`; `c4=1948` | (b) render p.18 |
| 18 | 261 | 4 | $\text{Sanbongi, Ohtani}^{(12)}$ | `c2=0.18`; `c3=1600`; `c4=1951` | (b) render p.18 |
| 18 | 261 | 4 | $\text{Sakagami}^{(2)}$ | `c2=0.16~0.17 (typed refusal)`; `c3=1600`; `c4=1953` | (b) render p.18 |
| 18 | 261 | 4 | $\text{Fulton, Chipman}^{(11)}$ | `c2=0.005`; `c3=1600`; `c4=1954` | (b) render p.18 |
| 18 | 261 | 4 | $\text{Fischer, Engelbrecht}^{(13)}$ | `c2=0.008`; `c3=1600`; `c4=1955` | (b) render p.18 |
| 18 | 261 | 4 | $\text{Baird, Taylor}^{(10)}$ | `c2=0.08`; `c3=1550`; `c4=1958` | (b) render p.18 |

## Reconciliations and interpretation

- Table 2 M is printed on CB-1 and ditto marks repeat it for the remaining rows. Table 3 row marks M, G, and paired marks were read row by row; the extract preserves M/G in the row value and resolves the container in point conditions.
- Table 3 contains six fixed-alumina series (T5–T30) and the separate AS-1–AS-5 saturation series. The paper’s mullite-and-corundum saturation statement (PDF p.9, printed p.252) applies only to the AS rows; the T5–T30 rows carry no saturation condition.
- The Table 2 and Table 3 activity standard is β-cristobalite at 1630°C (PDF p.14, printed p.257; Eq. 3-13). The activity coefficient relation γA=aA/NA is stated in the Discussion (PDF p.16, printed p.259); each coefficient is measured_reduced from its row's stored activity and SiO2 mole fraction, with a derived_from link to the activity row. Activity values remain quoted_attributed because the e.m.f. input has no schema quantity.
- Table 1 gives no reference state; it remains unknown. Table 4 gives no numeric liquid composition, and cited reference standard states are not specified here. The present-work Table 4 value is extrapolated from Fig. 3; figure values were not digitized.
- The reference-slag composition differs between PDF p.8 / printed p.251 (45.2% CaO, 2.2% SiO2, 53.6% Al2O3) and PDF p.14 / printed p.257 (45.2%, 2.2%, 52.6%). Both printed values were confirmed against renders; neither is used as a row composition.
- Table 2 row CB-7 c2 is clearly `32.0` wt% CaO in a 600 dpi render (PDF p.9), although the printed row's wt% cells sum to 91.5% and its mol% cells are 41.8/58.2. The source cell is preserved as printed and the discrepancy is not corrected.
- The source prints no experimental total pressure, fO2, or gas mix for the tabulated cell runs. These remain unknown. The argon note about Fe-Si preparation and the Sanbongi–Ohtani H2/H2O equilibrium are not propagated to the cell runs.
- All distinct (page, printed table) locators were checked against the page renders: (5,1), (9,2), (10,3), (11,3 continued), and (18,4). The focused test pins rendered cells on pages 5, 9, 10, and 18 after isolated migration.
- The table comparison output was generated from the same extract with only the transient comparison copy's phase token normalized from the schema token `l` to `liquid`, which the compare tool accepts. Extract values, row ids, and locators were unchanged. Comparison: 144 matched, 0 mismatch, 0 no-match, 0 needs_mapping.
