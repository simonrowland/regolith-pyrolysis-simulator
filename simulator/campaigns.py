"""
Campaign Manager — Sequencing, Ramp Rates & Endpoint Detection
===============================================================

Manages the progression of campaigns (C0 → C0b → C2A/C2B → C3 → C4 → C5 → C6),
temperature ramp profiles, atmosphere configuration, and endpoint detection
for each campaign phase.

Each campaign has:
    - Temperature target(s) and ramp rate (°C/hr)
    - Atmosphere settings (vacuum, pO₂, pN₂)
    - Endpoint criteria (IR signal decay, current decay, self-termination)
    - Next-campaign logic (may require operator decision)
"""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from simulator import mre_ladder
from simulator.lab_schedule import (
    LAB_SCHEDULE_OVERRIDE_KEY,
    LAB_SCHEDULE_PO2_SETPOINT_KEY,
    interpolate_schedule_points,
    normalize_lab_schedule,
    pO2_enforcement_row,
    pO2_setpoint_mbar_from_schedule,
    schedule_sample_time_h,
)
from simulator.furnace_materials import FURNACE_MAX_T_BOUNDS_C
from simulator.state import StirState, clamp_stir_factor, clamp_stir_state
from simulator.core import (
    Atmosphere, BatchRecord, CampaignPhase, CondensationTrain,
    DecisionPoint, DecisionType, EvaporationFlux, MeltState,
)

C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR = 0.01


class _CampaignOverrideFields(dict):
    def __init__(self,
                 campaign_name: str,
                 validator,
                 values: Mapping[str, object] | None = None):
        self._campaign_name = str(campaign_name)
        self._validator = validator
        super().__init__()
        if values:
            self.update(values)

    def _validate(self, fields: Mapping[str, object]) -> None:
        if fields:
            self._validator(self._campaign_name, fields)

    def __setitem__(self, key: str, value: object) -> None:
        field = str(key)
        self._validate({field: value})
        super().__setitem__(field, value)

    def setdefault(self, key: str, default: object = None):
        field = str(key)
        if field not in self:
            self._validate({field: default})
        return super().setdefault(field, default)

    def update(self, *args, **kwargs) -> None:
        fields = {
            str(key): value
            for key, value in dict(*args, **kwargs).items()
        }
        self._validate(fields)
        for key, value in fields.items():
            super().__setitem__(key, value)


class _CampaignOverrideStore(dict):
    def __init__(self, validator):
        self._validator = validator
        super().__init__()

    def _coerce_fields(self, campaign_name: str, value: object):
        if isinstance(value, _CampaignOverrideFields):
            return value
        if not isinstance(value, Mapping):
            raise ValueError(
                f'runtime_campaign_overrides[{campaign_name!r}] must be a mapping')
        return _CampaignOverrideFields(
            campaign_name,
            self._validator,
            value,
        )

    def __setitem__(self, key: str, value: object) -> None:
        campaign_name = str(key)
        super().__setitem__(
            campaign_name,
            self._coerce_fields(campaign_name, value),
        )

    def setdefault(self, key: str, default: object = None):
        campaign_name = str(key)
        if campaign_name not in self:
            self[campaign_name] = {} if default is None else default
        return super().__getitem__(campaign_name)

    def update(self, *args, **kwargs) -> None:
        for key, value in dict(*args, **kwargs).items():
            self[str(key)] = value


class CampaignManager:
    """
    Manages campaign sequencing and endpoint detection.

    Reads campaign parameters from setpoints.yaml and controls
    the transition between campaigns, including prompting for
    operator decisions (Path A/B, Branch One/Two).
    """

    def __init__(self, setpoints: dict):
        self.setpoints = setpoints
        self.campaigns = setpoints.get('campaigns', {})
        try:
            self.furnace_max_T_C = self._float(
                setpoints.get('furnace_max_T_C', 1800.0),
                1800.0,
            )
        except ValueError as exc:
            raise ValueError('furnace_max_T_C must be numeric') from exc
        if (
            not math.isfinite(self.furnace_max_T_C)
            or self.furnace_max_T_C < FURNACE_MAX_T_BOUNDS_C[0]
            or self.furnace_max_T_C > FURNACE_MAX_T_BOUNDS_C[1]
        ):
            # Grounding: docs-private/research/
            # 2026-06-18-furnace-max-temp/findings.md
            raise ValueError(
                'furnace_max_T_C must be finite and within '
                f'[{FURNACE_MAX_T_BOUNDS_C[0]:.0f}, {FURNACE_MAX_T_BOUNDS_C[1]:.0f}]'
            )
        # User-configurable overrides
        self.c4_max_temp_C = 1670.0  # Max T for C4 Mg pyrolysis (default)

        # Runtime overrides from UI (keyed by campaign name)
        # Structure: {'C2A': {'ramp_rate': 10.0, 'pO2_mbar': 1.0,
        #                     'stir_factor': 8.0, 'max_hours': 25}}
        self.overrides: Dict[str, dict] = _CampaignOverrideStore(
            type(self)._refuse_unknown_override_fields)
        self.last_pO2_enforcement: dict[str, object] | None = None
        self.c5_enabled = False
        self._c2a_staged_stage_idx: int = 0
        self._c2a_staged_stage_start_hour: int = 0
        self._c2a_staged_peak_flux_by_species: dict[str, float] = {}
        self._pending_c3_na_scoped_overrides: dict | None = None
        self._active_c3_na_scoped_overrides: dict | None = None

    _CONFIG_KEY_BY_PHASE = {
        CampaignPhase.C0B: 'C0b_p_cleanup',
        CampaignPhase.C2A: 'C2A_continuous',
        CampaignPhase.C2A_STAGED: 'C2A_staged',
        CampaignPhase.C3_K: 'C3',
        CampaignPhase.C3_NA: 'C3',
        CampaignPhase.C7_CA_ALUMINOTHERMIC: 'C7',
        CampaignPhase.MRE_BASELINE: 'mre_baseline',
    }

    _EXTRA_OVERRIDE_KEY_PHASES = {
        'C0b': (CampaignPhase.C0B,),
    }
    _OVERRIDE_CONSUMER_NAMES = frozenset({
        'campaign_overrides',
        'override',
        'overrides',
        'ovr',
        'runtime_override',
    })

    @staticmethod
    def _is_noninteractive_test_batch(record: BatchRecord) -> bool:
        return str(getattr(record, 'feedstock_key', '')).startswith('debug_')

    @staticmethod
    def _record_auto_decision(record: BatchRecord,
                              decision_type: DecisionType,
                              choice: str) -> None:
        decision = (decision_type, choice)
        if decision not in record.decisions:
            record.decisions.append(decision)

    def _campaign_config_key(self, campaign: CampaignPhase) -> str:
        return self._CONFIG_KEY_BY_PHASE.get(campaign, campaign.name)

    def _campaign_config(self, campaign: CampaignPhase) -> dict:
        cfg = self.campaigns.get(self._campaign_config_key(campaign), {})
        return cfg if isinstance(cfg, dict) else {}

    @classmethod
    def _campaign_phases_for_override_key(
            cls, campaign_name: str) -> Tuple[CampaignPhase, ...]:
        phases: list[CampaignPhase] = []
        try:
            phases.append(CampaignPhase[str(campaign_name)])
        except KeyError:
            pass
        phases.extend(
            phase
            for phase, key in cls._CONFIG_KEY_BY_PHASE.items()
            if key == str(campaign_name)
        )
        phases.extend(cls._EXTRA_OVERRIDE_KEY_PHASES.get(str(campaign_name), ()))
        unique: list[CampaignPhase] = []
        for phase in phases:
            if phase not in unique:
                unique.append(phase)
        return tuple(unique)

    @classmethod
    def known_override_fields(cls, campaign_name: str) -> Tuple[str, ...]:
        phases = cls._campaign_phases_for_override_key(str(campaign_name))
        if not phases:
            return ()
        return tuple(sorted(cls._derived_override_field_names()))

    @classmethod
    @lru_cache(maxsize=1)
    def _derived_override_field_names(cls) -> frozenset[str]:
        root = Path(__file__).resolve().parents[1]
        source_paths = (
            Path(__file__).resolve(),
            Path(__file__).with_name('lab_schedule.py').resolve(),
            root / 'simulator' / 'runner.py',
            root / 'web' / 'routes.py',
        )
        constants = {
            name: value
            for name, value in globals().items()
            if isinstance(value, str)
        }
        fields: set[str] = set()
        for source_path in source_paths:
            try:
                tree = ast.parse(source_path.read_text(encoding='utf-8'))
            except OSError:
                continue
            fields.update(cls._override_fields_from_ast(tree, constants))
        return frozenset(fields)

    @classmethod
    def _override_fields_from_ast(
            cls,
            tree: ast.AST,
            constants: Mapping[str, str]) -> set[str]:
        fields: set[str] = set()

        def literal_key(node: ast.AST) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name):
                value = constants.get(node.id)
                return value if isinstance(value, str) else None
            return None

        def is_consumer_name(node: ast.AST) -> bool:
            return isinstance(node, ast.Name) and node.id in cls._OVERRIDE_CONSUMER_NAMES

        def is_campaign_override_call(node: ast.AST) -> bool:
            return (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == '_campaign_overrides'
            )

        def is_consumer_node(node: ast.AST) -> bool:
            return is_consumer_name(node) or is_campaign_override_call(node)

        def collect_key(node: ast.AST) -> None:
            key = literal_key(node)
            if key:
                fields.add(key)

        class Visitor(ast.NodeVisitor):
            def visit_Call(self, node: ast.Call) -> None:
                func = node.func
                if (isinstance(func, ast.Attribute)
                        and func.attr in {'get', 'setdefault'}
                        and is_consumer_node(func.value)
                        and node.args):
                    collect_key(node.args[0])
                if (isinstance(func, ast.Name)
                        and func.id == '_first_present'
                        and node.args
                        and is_consumer_node(node.args[0])):
                    for arg in node.args[1:]:
                        collect_key(arg)
                self.generic_visit(node)

            def visit_Subscript(self, node: ast.Subscript) -> None:
                if is_consumer_node(node.value):
                    collect_key(node.slice)
                self.generic_visit(node)

            def visit_Compare(self, node: ast.Compare) -> None:
                for op, comparator in zip(node.ops, node.comparators):
                    if isinstance(op, ast.In):
                        if is_consumer_node(comparator):
                            collect_key(node.left)
                        elif is_consumer_node(node.left):
                            collect_key(comparator)
                self.generic_visit(node)

        Visitor().visit(tree)
        return fields

    @classmethod
    def validate_runtime_campaign_overrides(
            cls,
            overrides: Mapping[str, Mapping[str, object]]) -> None:
        if not isinstance(overrides, Mapping):
            raise ValueError('runtime_campaign_overrides must be a mapping')
        for campaign_name, fields in overrides.items():
            if not isinstance(fields, Mapping):
                raise ValueError(
                    f'runtime_campaign_overrides[{campaign_name!r}] '
                    'must be a mapping')
            cls._refuse_unknown_override_fields(str(campaign_name), fields)

    @classmethod
    def _refuse_unknown_override_fields(
            cls,
            campaign_name: str,
            fields: Mapping[str, object]) -> None:
        known_fields = cls.known_override_fields(campaign_name)
        if not known_fields:
            known_campaigns = sorted({
                phase.name for phase in CampaignPhase
                if phase not in (CampaignPhase.IDLE, CampaignPhase.COMPLETE)
            } | set(cls._CONFIG_KEY_BY_PHASE.values())
              | set(cls._EXTRA_OVERRIDE_KEY_PHASES))
            raise ValueError(
                f'unknown runtime_campaign_overrides campaign '
                f'{campaign_name!r}; known campaigns: '
                f'{", ".join(known_campaigns)}')
        known = set(known_fields)
        unknown = sorted(str(field) for field in fields if str(field) not in known)
        if unknown:
            raise ValueError(
                f'unknown runtime_campaign_overrides[{campaign_name!r}].'
                f'{unknown[0]}; known overridable fields for '
                f'{campaign_name}: {", ".join(known_fields)}')

    def _campaign_overrides(self, campaign: CampaignPhase) -> dict:
        merged: dict = {}
        if (
            campaign == CampaignPhase.C3_NA
            and self._active_c3_na_scoped_overrides
        ):
            merged.update(self._active_c3_na_scoped_overrides)
        for key in (self._campaign_config_key(campaign), campaign.name):
            ovr = self.overrides.get(key, {})
            if isinstance(ovr, dict):
                self._refuse_unknown_override_fields(key, ovr)
                if 'setpoints' in ovr:
                    merged.setdefault('setpoints', ovr['setpoints'])
                merged.update(ovr)
        return merged

    @staticmethod
    def _float(value, default: float) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Invalid numeric campaign setpoint: {value!r}') from exc

    def _required_float(self, value, label: str) -> float:
        if value is None:
            raise ValueError(f'Missing numeric campaign setpoint: {label}')
        return self._float(value, 0.0)

    def _campaign_rate_band_midpoint(
        self,
        campaign: CampaignPhase,
        band_key: str,
    ) -> float:
        campaign_key = self._campaign_config_key(campaign)
        label = f'{campaign_key}.dT_dt_C_per_hr.{band_key}'
        rate_bands = self._campaign_config(campaign).get('dT_dt_C_per_hr')
        if not isinstance(rate_bands, Mapping):
            raise ValueError(
                f'Missing campaign rate bands: {campaign_key}.dT_dt_C_per_hr'
            )
        band = rate_bands.get(band_key)
        if (
            not isinstance(band, (list, tuple))
            or len(band) != 2
        ):
            raise ValueError(
                f'Malformed campaign rate band {label}: expected [low, high]'
            )
        low = self._required_float(band[0], f'{label}[0]')
        high = self._required_float(band[1], f'{label}[1]')
        if not math.isfinite(low) or not math.isfinite(high):
            raise ValueError(f'Malformed campaign rate band {label}: non-finite')
        return (low + high) / 2.0

    def _scalar_config_float(self,
                             config: Mapping[str, object],
                             key: str) -> float | None:
        if key not in config:
            return None
        value = config.get(key)
        if value is None or isinstance(value, (list, tuple, Mapping)):
            return None
        return self._float(value, 0.0)

    def _pressure_config_float(self,
                               config: Mapping[str, object],
                               scalar_key: str,
                               default_key: str,
                               default: float) -> float:
        scalar_value = self._scalar_config_float(config, scalar_key)
        if scalar_value is not None:
            return scalar_value
        return self._float(config.get(default_key), default)

    def _configured_max_hold_hr(self,
                                campaign: CampaignPhase,
                                *path: str) -> float:
        value = self._campaign_config(campaign).get('max_hold_hr')
        label = f"{self._campaign_config_key(campaign)}.max_hold_hr"
        for key in path:
            label = f"{label}.{key}"
            if not isinstance(value, Mapping) or key not in value:
                raise ValueError(f'Missing campaign max_hold_hr setpoint: {label}')
            value = value[key]
        return self._required_float(value, label)

    def _max_hold_hr(self, campaign: CampaignPhase, *path: str) -> float:
        if not path:
            ovr = self._campaign_overrides(campaign)
            if 'max_hours' in ovr:
                return self._float(ovr.get('max_hours'), 0.0)
            if 'hold_time_h' in ovr:
                return self._float(ovr.get('hold_time_h'), 0.0)
            if 'duration_h' in ovr:
                return self._float(ovr.get('duration_h'), 0.0)
        return self._configured_max_hold_hr(campaign, *path)

    def _configured_endpoint(self,
                             campaign: CampaignPhase,
                             key: str) -> Mapping:
        value = self._campaign_config(campaign).get(key, {})
        if not isinstance(value, Mapping):
            label = f"{self._campaign_config_key(campaign)}.{key}"
            raise ValueError(f'Invalid campaign endpoint setpoint: {label}')
        return value

    def _endpoint_float(self,
                        campaign: CampaignPhase,
                        endpoint: Mapping,
                        key: str) -> float:
        label = f"{self._campaign_config_key(campaign)}.{key}"
        return self._required_float(endpoint.get(key), label)

    def _configured_staged_max_hold_hr(self,
                                       campaign: CampaignPhase) -> float:
        max_hold_hr = self._configured_max_hold_hr(campaign)
        stages = self._campaign_config(campaign).get('stages', [])
        total_hours = 0
        if isinstance(stages, list):
            for stage in stages:
                if isinstance(stage, dict):
                    total_hours += max(
                        1, int(self._float(stage.get('duration_h'), 1.0)))
        if total_hours and max_hold_hr != total_hours:
            key = self._campaign_config_key(campaign)
            raise ValueError(
                f'{key}.max_hold_hr must match summed stage duration_h')
        return max_hold_hr

    def _c2a_staged_depletion_flux_decay_fraction(self) -> float:
        cfg = self._campaign_config(CampaignPhase.C2A_STAGED)
        ovr = self._campaign_overrides(CampaignPhase.C2A_STAGED)
        raw = ovr.get(
            'depletion_flux_decay_fraction',
            cfg.get('depletion_flux_decay_fraction', 0.0),
        )
        if raw is None:
            return 0.0
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                'C2A_staged.depletion_flux_decay_fraction must be numeric'
            ) from exc
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(
                'C2A_staged.depletion_flux_decay_fraction must be finite and non-negative'
            )
        if value <= 0.0:
            return 0.0
        return max(value, C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR)

    def _c2a_staged_enabled_stages(self) -> list[dict]:
        cfg = self._campaign_config(CampaignPhase.C2A_STAGED)
        stages = cfg.get('stages')
        if not isinstance(stages, list) or not stages:
            raise ValueError('C2A_staged.stages must be a non-empty list')
        enabled: list[dict] = []
        for idx, stage in enumerate(stages):
            if not isinstance(stage, dict):
                raise ValueError(f'C2A_staged.stages[{idx}] must be a mapping')
            enabled.append(stage)
        return enabled

    def _c2a_staged_current_stage(self) -> dict | None:
        stages = self._c2a_staged_enabled_stages()
        idx = min(max(0, int(self._c2a_staged_stage_idx)), len(stages) - 1)
        self._c2a_staged_stage_idx = idx
        return stages[idx]

    def _c2a_staged_c3_na_scoped_overrides(self) -> dict:
        cfg = self._campaign_config(CampaignPhase.C2A_STAGED)
        na_stage = cfg.get('na_shuttle_stage', {})
        if not isinstance(na_stage, dict):
            na_stage = cfg.get('k_shuttle_stage', {})
        if not isinstance(na_stage, dict):
            return {}
        target = self._float(na_stage.get('target_C'), 1150.0)
        return {
            'inject_target_C': target,
            'bakeout_target_C': target,
            'ramp_rate': self._float(na_stage.get('ramp_rate_C_per_hr'), 600.0),
            'staged_duration_h': self._float(na_stage.get('duration_h'), 3.0),
        }

    def _c2a_staged_stage_by_hour(
        self,
        campaign_hour: int,
        stages: list,
    ) -> dict | None:
        if not isinstance(stages, list) or not stages:
            return None
        hour = max(0, int(campaign_hour))
        elapsed = 0
        selected = stages[-1]
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            duration = max(1, int(self._float(stage.get('duration_h'), 1.0)))
            if hour < elapsed + duration:
                selected = stage
                break
            elapsed += duration
        return selected if isinstance(selected, dict) else None

    def _c2a_staged_active_stage(self, campaign_hour: int) -> dict | None:
        stages = self._c2a_staged_enabled_stages()
        if self._c2a_staged_depletion_flux_decay_fraction() <= 0.0:
            return self._c2a_staged_stage_by_hour(campaign_hour, stages)
        idx = min(max(0, int(self._c2a_staged_stage_idx)), len(stages) - 1)
        return stages[idx]

    def _c2a_staged_flux_decay_species(self, stage: Mapping) -> tuple[str, ...]:
        endpoint = stage.get('endpoint', {})
        if not isinstance(endpoint, Mapping):
            return ()
        raw = endpoint.get('flux_decay_species', ())
        if raw in (None, ''):
            return ()
        if isinstance(raw, str):
            return (raw,)
        try:
            return tuple(str(species) for species in raw if str(species))
        except TypeError as exc:
            raise ValueError('endpoint.flux_decay_species must be a sequence') from exc

    # ------------------------------------------------------------------
    # Campaign configuration
    # ------------------------------------------------------------------

    def configure_campaign(self, melt: MeltState, campaign: CampaignPhase):
        """
        Set gas-side atmosphere and process parameters for a campaign.
        ``melt.fO2_log`` is engine-computed from melt composition per tick.

        Called when starting a new campaign phase.
        """
        if campaign == CampaignPhase.C3_NA:
            self._active_c3_na_scoped_overrides = (
                self._pending_c3_na_scoped_overrides
            )
            self._pending_c3_na_scoped_overrides = None
        else:
            self._pending_c3_na_scoped_overrides = None
            self._active_c3_na_scoped_overrides = None

        if campaign == CampaignPhase.C2A_STAGED:
            self._c2a_staged_stage_idx = 0
            self._c2a_staged_stage_start_hour = 0
            self._c2a_staged_peak_flux_by_species = {}

        if campaign == CampaignPhase.C0:
            ambient_pressure = max(
                0.0, float(getattr(melt, 'ambient_pressure_mbar', 0.0) or 0.0))
            if ambient_pressure > 0:
                melt.atmosphere = Atmosphere.CO2_BACKPRESSURE
                melt.p_total_mbar = ambient_pressure
            else:
                melt.atmosphere = Atmosphere.HARD_VACUUM
                melt.p_total_mbar = 0.0
            melt.pO2_mbar = 0.0

        elif campaign == CampaignPhase.C0B:
            cfg = self._campaign_config(campaign)
            melt.atmosphere = Atmosphere.CONTROLLED_O2_FLOW
            melt.pO2_mbar = self._pressure_config_float(
                cfg, 'pO2_mbar', 'pO2_mbar_default', 9.0)
            melt.p_total_mbar = self._pressure_config_float(
                cfg, 'p_total_mbar', 'p_total_mbar_default', 9.0)

        elif campaign in (CampaignPhase.C2A, CampaignPhase.C2A_STAGED):
            cfg = self._campaign_config(campaign)
            melt.atmosphere = Atmosphere.PN2_SWEEP
            melt.pO2_mbar = self._pressure_config_float(
                cfg, 'pO2_mbar', 'pO2_mbar_default', 0.0)
            melt.p_total_mbar = self._pressure_config_float(
                cfg, 'p_total_mbar', 'p_total_mbar_default', 10.0)

        elif campaign == CampaignPhase.C2B:
            cfg = self._campaign_config(campaign)
            melt.atmosphere = Atmosphere.CONTROLLED_O2
            melt.pO2_mbar = self._pressure_config_float(
                cfg, 'pO2_mbar', 'pO2_mbar_default', 1.5)
            melt.p_total_mbar = self._pressure_config_float(
                cfg, 'p_total_mbar', 'p_total_mbar_default', 1.5)

        elif campaign in (CampaignPhase.C3_K, CampaignPhase.C3_NA):
            cfg = self._campaign_config(campaign)
            melt.atmosphere = Atmosphere.CONTROLLED_O2
            melt.pO2_mbar = self._pressure_config_float(
                cfg, 'pO2_mbar', 'pO2_mbar_default', 1.0)
            melt.p_total_mbar = self._pressure_config_float(
                cfg, 'p_total_mbar', 'p_total_mbar_default', 1.0)

        elif campaign == CampaignPhase.C4:
            cfg = self._campaign_config(campaign)
            melt.atmosphere = Atmosphere.CONTROLLED_O2
            melt.pO2_mbar = self._pressure_config_float(
                cfg, 'pO2_mbar', 'pO2_mbar_default', 0.2)
            melt.p_total_mbar = self._pressure_config_float(
                cfg, 'p_total_mbar', 'p_total_mbar_default', 0.2)

        elif campaign == CampaignPhase.C5:
            cfg = self._campaign_config(campaign)
            melt.atmosphere = Atmosphere.O2_BACKPRESSURE
            melt.pO2_mbar = self._pressure_config_float(
                cfg, 'pO2_mbar', 'pO2_mbar_default', 50.0)
            melt.p_total_mbar = self._pressure_config_float(
                cfg, 'p_total_mbar', 'p_total_mbar_default', 50.0)

        elif campaign == CampaignPhase.C6:
            cfg = self._campaign_config(campaign)
            melt.atmosphere = Atmosphere.CONTROLLED_O2
            melt.pO2_mbar = self._pressure_config_float(
                cfg, 'pO2_mbar', 'pO2_mbar_default', 0.2)
            melt.p_total_mbar = self._pressure_config_float(
                cfg, 'p_total_mbar', 'p_total_mbar_default', 0.2)

        elif campaign == CampaignPhase.C7_CA_ALUMINOTHERMIC:
            cfg = self._campaign_config(campaign)
            melt.atmosphere = Atmosphere.HARD_VACUUM
            melt.pO2_mbar = self._pressure_config_float(
                cfg, 'pO2_mbar', 'pO2_mbar_default', 0.0)
            melt.p_total_mbar = self._pressure_config_float(
                cfg, 'p_total_mbar', 'p_total_mbar_default', 0.05)

        elif campaign == CampaignPhase.MRE_BASELINE:
            cfg = self._campaign_config(campaign)
            melt.atmosphere = Atmosphere.O2_BACKPRESSURE
            melt.pO2_mbar = self._pressure_config_float(
                cfg, 'pO2_mbar', 'pO2_mbar_default', 50.0)
            melt.p_total_mbar = self._pressure_config_float(
                cfg, 'p_total_mbar', 'p_total_mbar_default', 50.0)

        # Apply runtime overrides (pO₂, stir_factor)
        ovr = self._campaign_overrides(campaign)
        if 'pO2_mbar' in ovr:
            # 0.5.4 W5 milestone-review P1 (codex /challenge
            # 2026-05-28): mirror the active-path atmosphere switch
            # at ``simulator/session.py:276-298`` here too. Pre-fix
            # an operator who set
            # ``session.adjust("campaign_override",
            # campaign="C2A", field="pO2_mbar", value=1.0)`` while
            # C0 was active stored the override correctly (active
            # campaign C0 unaffected), but when C2A later became
            # active via ``configure_campaign()`` the override was
            # applied as a bare ``melt.pO2_mbar`` write — without
            # switching ``melt.atmosphere`` away from the C2A
            # default ``PN2_SWEEP``. Result: commanded-pO2 floor
            # stays disabled because PN2_SWEEP isn't in
            # ``_O2_CONTROLLED_ATMOSPHERES``. Now: a positive
            # override pO2 forces atmosphere to CONTROLLED_O2 at
            # transition time too, restoring the SiO suppression
            # lever's transition-time consistency with the
            # active-path fix.  ``pO2_mbar == 0`` leaves atmosphere
            # alone (operator clearing the setpoint, NOT requesting
            # controlled-O2).
            override_pO2 = float(ovr['pO2_mbar'])
            melt.pO2_mbar = override_pO2
            melt.p_total_mbar = max(melt.p_total_mbar, melt.pO2_mbar)
            if override_pO2 > 0.0:
                melt.atmosphere = Atmosphere.CONTROLLED_O2
        self.apply_lab_schedule_controls(melt, campaign, sample_time_h=0.0)
        # 0.5.3 Phase B chunk-review P2 (codex 2026-05-28): per-axis
        # merge precedence. Before this fix, when an operator passed
        # BOTH ``{stir_factor: 6, stir_state: {radial: 8}}``, the whole-
        # ``stir_state`` write erased the explicit axial=6 (because
        # ``clamp_stir_state({radial: 8})`` defaults the missing axial
        # to 1.0 laminar). That's not what the operator meant — they
        # asked for axial=6 (from stir_factor) AND radial=8 (from
        # stir_state.radial). Resolve per-axis instead of whole-dict:
        #
        #   1. ``stir_factor`` (if present) sets the axial axis.
        #   2. ``stir_state`` (if present) sets the radial axis, AND
        #      overrides axial ONLY if it explicitly carries an axial
        #      key. Otherwise axial keeps the stir_factor value from
        #      step 1 (or the prior melt.stir_state.axial if neither
        #      override is supplied).
        has_stir_factor = 'stir_factor' in ovr
        has_stir_state = 'stir_state' in ovr
        if has_stir_factor:
            # 0.5.2 Phase B P1: route through ``clamp_stir_factor`` so
            # campaign YAML overrides honour ``MAX_STIR_FACTOR``.
            # 0.5.3 Phase B: ``stir_factor`` field touches AXIAL only
            # (via the backward-compat property setter on MeltState).
            melt.stir_factor = clamp_stir_factor(ovr['stir_factor'])
        if has_stir_state:
            raw_state = ovr['stir_state']
            new_state = clamp_stir_state(raw_state)
            # Per-axis merge: when both override fields are present and
            # ``stir_state`` does NOT explicitly carry axial, preserve
            # the ``stir_factor``-set axial. The dict-shape check is
            # the only reliable "did the operator mention axial?"
            # signal we have — a StirState or scalar input is
            # whole-dataclass-replaces by design.
            if (has_stir_factor
                    and isinstance(raw_state, Mapping)
                    and 'axial' not in raw_state):
                # Keep the axial value just set by stir_factor; replace
                # only radial from stir_state. Construct a fresh
                # StirState to honour the dataclass invariants.
                melt.stir_state = StirState(
                    axial=melt.stir_state.axial,
                    radial=new_state.radial,
                )
            else:
                # Either no concurrent stir_factor, or stir_state
                # explicitly named axial — whole-dataclass replace.
                melt.stir_state = new_state

        melt.validate_melt_pressures()

    # ------------------------------------------------------------------
    # Temperature ramp
    # ------------------------------------------------------------------

    def get_temp_target(self, campaign: CampaignPhase,
                        campaign_hour: int,
                        melt: MeltState) -> Tuple[Optional[float], float]:
        """
        Get the target temperature and ramp rate for a campaign.

        Returns:
            (target_T_C, ramp_rate_C_per_hr)
            target_T is None for isothermal holds or MRE campaigns.
        """
        result = self._get_base_temp_target(campaign, campaign_hour, melt)
        target_T, ramp_rate = self._apply_ramp_override(campaign, result[0], result[1])
        return (self._clamp_to_furnace_max(target_T), ramp_rate)

    def _clamp_to_furnace_max(self, target_T: Optional[float]) -> Optional[float]:
        if target_T is None:
            return None
        return min(target_T, self.furnace_max_T_C)

    def _get_base_temp_target(self, campaign: CampaignPhase,
                               campaign_hour: int,
                               melt: MeltState) -> Tuple[Optional[float], float]:
        """Base temperature targets before runtime overrides."""
        lab_schedule = self._lab_schedule(campaign)
        if lab_schedule is not None:
            sample_time_h = schedule_sample_time_h(lab_schedule, campaign_hour)
            target = interpolate_schedule_points(
                lab_schedule['melt_temperature_C'],
                sample_time_h,
            )
            return (target, abs(float(target) - float(melt.temperature_C)))

        thermal_window = self._thermal_window_temp_target(campaign, melt)
        if thermal_window is not None:
            return thermal_window

        if campaign == CampaignPhase.C0:
            # Ramp from current T to 950°C at 50°C/hr
            return (950.0, 50.0)

        elif campaign == CampaignPhase.C0B:
            # Isothermal hold at midpoint of [1180, 1320]
            return (1250.0, 30.0)

        elif campaign == CampaignPhase.C2A:
            # Continuous ramp 1050 C -> furnace_max_T_C
            # Ramp rate varies by YAML band midpoint.
            if melt.temperature_C < 1320:
                ramp = self._campaign_rate_band_midpoint(
                    campaign, 'early_ramp_1050_1320C')
            else:
                ramp = self._campaign_rate_band_midpoint(
                    campaign, 'peak_SiO_window_1400_1600C')
            return (self.furnace_max_T_C, ramp)

        elif campaign == CampaignPhase.C2A_STAGED:
            if self._c2a_staged_depletion_flux_decay_fraction() <= 0.0:
                cfg = self._campaign_config(campaign)
                stages = self._c2a_staged_enabled_stages()
                selected = self._c2a_staged_stage_by_hour(campaign_hour, stages)
                if selected is None:
                    raise ValueError('C2A_staged.stages did not select a stage')

                if selected.get('name') == 'fe_hot_hold':
                    ovr = self._campaign_overrides(campaign)
                    target = self._float(
                        ovr.get('hold_temp_C'),
                        self._float(cfg.get('default_hold_T_C'), 1750.0),
                    )
                else:
                    target = self._float(selected.get('target_C'), 1750.0)
                ramp = self._float(selected.get('ramp_rate_C_per_hr'), 150.0)
                return (target, ramp)

            cfg = self._campaign_config(campaign)
            selected = self._c2a_staged_current_stage()
            if selected is None:
                raise ValueError('C2A_staged.stages did not select a stage')
            if selected.get('name') == 'fe_hot_hold':
                ovr = self._campaign_overrides(campaign)
                target = self._float(
                    ovr.get('hold_temp_C'),
                    self._float(cfg.get('default_hold_T_C'), 1750.0),
                )
            else:
                target = self._float(selected.get('target_C'), 1750.0)
            ramp = self._float(selected.get('ramp_rate_C_per_hr'), 150.0)
            return (target, ramp)

        elif campaign == CampaignPhase.C2B:
            # Ramp 1320 → 1480°C (pO₂-controlled)
            return (1480.0, 10.0)

        elif campaign in (CampaignPhase.C3_K, CampaignPhase.C3_NA):
            # Legacy C3 alternates injection/bakeout. V1c staged Na cleanup
            # overrides both targets to the cool FeO window near 1150 C.
            # Alternate between injection T and bakeout T
            ovr = self._campaign_overrides(campaign)
            inject_target = self._float(ovr.get('inject_target_C'), 1275.0)
            bakeout_target = self._float(ovr.get('bakeout_target_C'), 1600.0)
            ramp_rate = self._float(ovr.get('ramp_rate'), 50.0)
            cycle_period = 6  # hours per inject-bakeout cycle
            if campaign_hour % cycle_period < 3:
                return (inject_target, ramp_rate)  # injection phase
            else:
                return (bakeout_target, ramp_rate)  # bakeout phase

        elif campaign == CampaignPhase.C4:
            # Mg pyrolysis at 1580 up to user-configurable max T
            # Higher T → more Mg extraction but risk of freezing
            # refractory-enriched melt (liquidus rises as composition
            # becomes more aluminous/calcic after Fe/Ti/SiO₂ removal)
            ovr = self._campaign_overrides(campaign)
            target = self._float(
                ovr.get('hold_temp_C', ovr.get('hold_temperature_C')),
                self.c4_max_temp_C,
            )
            return (target, 10.0)

        elif campaign == CampaignPhase.C5:
            # MRE: hold at process temperature
            return (1575.0, 5.0)

        elif campaign == CampaignPhase.C6:
            # Mg/Al crossover is ~1573 C under V1c JANAF constants.
            return (1500.0, 10.0)

        elif campaign == CampaignPhase.C7_CA_ALUMINOTHERMIC:
            cfg = self._campaign_config(campaign)
            ovr = self._campaign_overrides(campaign)
            target = self._float(
                ovr.get('hold_temp_C', ovr.get('hold_temperature_C')),
                self._float(cfg.get('default_hold_T_C'), 1200.0),
            )
            return (target, 10.0)

        elif campaign == CampaignPhase.MRE_BASELINE:
            # Standard MRE: heat to melting then hold
            return (1575.0, 20.0)

        return (None, 0.0)

    def _thermal_window_temp_target(
            self,
            campaign: CampaignPhase,
            melt: MeltState) -> Optional[Tuple[Optional[float], float]]:
        ovr = self._campaign_overrides(campaign)
        keys = {
            'thermal_window_low_C',
            'thermal_window_high_C',
            'thermal_window_duration_h',
        }
        present = keys & set(ovr)
        if not present:
            return None
        if present != keys:
            missing = ', '.join(sorted(keys - present))
            raise ValueError(f'thermal window override missing: {missing}')
        low_C = self._float(ovr.get('thermal_window_low_C'), 0.0)
        high_C = self._float(ovr.get('thermal_window_high_C'), 0.0)
        duration_h = self._float(ovr.get('thermal_window_duration_h'), 0.0)
        if duration_h <= 0.0:
            raise ValueError('thermal_window_duration_h must be positive')
        if high_C < low_C:
            raise ValueError('thermal_window_high_C must be >= thermal_window_low_C')
        preheat_hours = self._float(ovr.get('thermal_window_preheat_hours'), 0.0)
        if preheat_hours < 0.0:
            raise ValueError('thermal_window_preheat_hours must be non-negative')
        if melt.temperature_C < low_C - 1e-9:
            return (
                low_C,
                self._float(
                    ovr.get('thermal_window_preheat_ramp_C_per_hr'),
                    600.0,
                ),
            )
        return (
            high_C,
            self._float(
                ovr.get('thermal_window_ramp_C_per_hr'),
                (high_C - low_C) / duration_h,
            ),
        )

    def _lab_schedule(self, campaign: CampaignPhase) -> Optional[Mapping]:
        raw = self._campaign_overrides(campaign).get(LAB_SCHEDULE_OVERRIDE_KEY)
        if raw is None:
            return None
        if isinstance(raw, Mapping):
            return normalize_lab_schedule(raw)
        raise ValueError('lab_schedule_must_be_mapping')

    def apply_lab_schedule_controls(
            self,
            melt: MeltState,
            campaign: CampaignPhase,
            *,
            sample_time_h: float) -> None:
        lab_schedule = self._lab_schedule(campaign)
        if lab_schedule is None:
            self.last_pO2_enforcement = None
            return
        ovr = self._campaign_overrides(campaign)
        total_pressure = interpolate_schedule_points(
            lab_schedule['chamber_pressure_mbar'],
            sample_time_h,
        )
        pO2_setpoint = pO2_setpoint_mbar_from_schedule(
            lab_schedule,
            ovr,
            total_pressure,
        )
        row = pO2_enforcement_row(
            hour=int(melt.hour) + (0 if sample_time_h <= 0.0 else 1),
            schedule=lab_schedule,
            schedule_time_h=float(sample_time_h),
            setpoint_mbar=pO2_setpoint,
            total_pressure_mbar=total_pressure,
        )
        background_gas = lab_schedule.get('gas_boundary', {}).get(
            'background_gas', {})
        background_species = ''
        background_fraction = 0.0
        if (
            isinstance(background_gas, Mapping)
            and str(background_gas.get('reported_status', '') or '') != 'not_reported'
        ):
            background_species = str(background_gas.get('species') or '').strip()
            try:
                background_fraction = float(
                    background_gas.get('mole_fraction', 1.0))
            except (TypeError, ValueError):
                background_fraction = 0.0
            if background_fraction < 0.0:
                background_fraction = 0.0
            elif background_fraction > 1.0:
                background_fraction = 1.0
        melt.p_total_mbar = float(total_pressure)
        melt.pO2_mbar = float(row['achieved_mbar'])
        melt.background_gas_species = background_species
        melt.background_gas_mole_fraction = background_fraction
        if melt.pO2_mbar > 0.0:
            melt.atmosphere = Atmosphere.CONTROLLED_O2
        elif melt.p_total_mbar > 0.0:
            melt.atmosphere = Atmosphere.PN2_SWEEP
        else:
            melt.atmosphere = Atmosphere.HARD_VACUUM
        self.last_pO2_enforcement = dict(row)
        melt.validate_melt_pressures()

    def _apply_ramp_override(self, campaign: CampaignPhase,
                             target_T: Optional[float],
                             ramp_rate: float) -> Tuple[Optional[float], float]:
        """Apply runtime ramp rate override if set."""
        ovr = self._campaign_overrides(campaign)
        if 'ramp_rate' in ovr:
            ramp_rate = float(ovr['ramp_rate'])
        elif 'temperature_ramp_C_per_h' in ovr:
            ramp_rate = float(ovr['temperature_ramp_C_per_h'])
        elif 'ramp_rate_C_per_h' in ovr:
            ramp_rate = float(ovr['ramp_rate_C_per_h'])
        return (target_T, ramp_rate)

    # ------------------------------------------------------------------
    # Endpoint detection
    # ------------------------------------------------------------------

    def check_endpoint(self, melt: MeltState,
                       evap_flux: EvaporationFlux,
                       train: CondensationTrain,
                       record: BatchRecord) -> bool:
        """
        Check if the current campaign has reached its endpoint.

        Endpoints are defined by:
        - C0:   IR signal decay < 5% of peak (volatile emission stops)
        - C0b:  P-species IR decay < 5% of peak
        - C2A:  Na/K/Fe/SiO all decay to < 5% peak
        - C2B:  Fe signal decays to < 5% peak
        - C3:   pO₂ returns to setpoint, holds 30 min
        - C4:   Mg signal decays to background
        - C5:   Current decays to < 10 A at target voltage
        - C6:   Self-terminating (liquidus > 1700°C)

        For the simulator, we approximate these with simpler checks
        based on evaporation rate thresholds and duration limits.

        Returns True if the campaign should end.
        """
        campaign = melt.campaign

        # Check user-specified max_hours override first
        ovr = self._campaign_overrides(campaign)
        if 'max_hours' in ovr:
            max_h = float(ovr['max_hours'])
            if max_h > 0 and melt.campaign_hour >= max_h:
                return True

        if campaign == CampaignPhase.C0:
            soft = self._configured_endpoint(campaign, 'soft_endpoint')
            min_temperature_C = self._endpoint_float(
                campaign, soft, 'temperature_min_C')
            min_hold_hr = self._float(
                ovr.get('min_hold_hr'),
                self._endpoint_float(campaign, soft, 'min_hold_hr'),
            )
            max_hold_hr = self._max_hold_hr(campaign)
            if (melt.temperature_C >= min_temperature_C
                    and melt.campaign_hour >= min_hold_hr):
                return True
            if melt.campaign_hour >= max_hold_hr:
                return True

        elif campaign == CampaignPhase.C0B:
            soft = self._configured_endpoint(campaign, 'soft_endpoint')
            max_hold_hr = self._max_hold_hr(campaign)
            min_temperature_C = self._endpoint_float(
                campaign, soft, 'temperature_min_C')
            if (melt.campaign_hour >= max_hold_hr
                    and melt.temperature_C >= min_temperature_C):
                return True

        elif campaign == CampaignPhase.C2A:
            soft = self._configured_endpoint(campaign, 'soft_endpoint')
            min_hold_hr = self._float(
                ovr.get('min_hold_hr'),
                self._endpoint_float(campaign, soft, 'min_hold_hr'),
            )
            threshold_kg_hr = self._float(
                ovr.get('threshold_kg_hr'),
                self._endpoint_float(campaign, soft, 'threshold_kg_hr'),
            )
            max_hold_hr = self._max_hold_hr(campaign)
            total_rate = evap_flux.total_kg_hr
            if (melt.campaign_hour >= min_hold_hr
                    and total_rate < threshold_kg_hr):
                return True
            if melt.campaign_hour >= max_hold_hr:
                return True

        elif campaign == CampaignPhase.C2A_STAGED:
            max_hold_hr = self._configured_staged_max_hold_hr(campaign)
            fraction = self._c2a_staged_depletion_flux_decay_fraction()
            if fraction <= 0.0:
                if melt.campaign_hour + 1 >= max_hold_hr:
                    return True
                return False

            stages = self._c2a_staged_enabled_stages()
            if not stages:
                if melt.campaign_hour + 1 >= max_hold_hr:
                    return True
                return False
            stage_idx = min(
                max(0, int(self._c2a_staged_stage_idx)),
                len(stages) - 1,
            )
            self._c2a_staged_stage_idx = stage_idx
            stage = stages[stage_idx]
            species = self._c2a_staged_flux_decay_species(stage)
            for species_name in species:
                raw_current = evap_flux.species_kg_hr.get(species_name, 0.0)
                current = max(0.0, self._float(raw_current, 0.0))
                previous = self._c2a_staged_peak_flux_by_species.get(
                    species_name,
                    0.0,
                )
                if current > previous:
                    self._c2a_staged_peak_flux_by_species[species_name] = current

            endpoint = stage.get('endpoint', {})
            if not isinstance(endpoint, Mapping):
                endpoint = {}
            stage_elapsed_h = melt.campaign_hour - self._c2a_staged_stage_start_hour + 1
            duration_h = max(1, int(self._float(stage.get('duration_h'), 1.0)))
            min_hold_h = self._float(
                endpoint.get('min_hold_h', endpoint.get('min_hold_hr')),
                1.0,
            )
            # Gate only on flux_decay_species that have actually EVOLVED in this
            # stage (peak > 0). A listed species that never evolves (e.g. K in a
            # K-poor feedstock) is intentionally NOT a gate — otherwise the stage
            # would wait for a flux that never comes and only the duration_h
            # timeout could end it. For co-evolving listed species (e.g. Na+K),
            # ALL that have peaked must decay below the fraction before advancing.
            observed_peaks = {
                species_name: peak
                for species_name, peak in self._c2a_staged_peak_flux_by_species.items()
                if species_name in species and peak > 0.0
            }
            flux_depleted = (
                stage_elapsed_h >= min_hold_h
                and bool(observed_peaks)
                and all(
                    max(
                        0.0,
                        self._float(
                            evap_flux.species_kg_hr.get(species_name, 0.0),
                            0.0,
                        ),
                    ) <= fraction * peak
                    for species_name, peak in observed_peaks.items()
                )
            )
            stage_timeout = stage_elapsed_h >= duration_h
            final_stage = stage_idx >= len(stages) - 1
            if flux_depleted or stage_timeout:
                if final_stage:
                    return True
                self._c2a_staged_stage_idx = stage_idx + 1
                self._c2a_staged_stage_start_hour = melt.campaign_hour + 1
                self._c2a_staged_peak_flux_by_species = {}
                return False
            if melt.campaign_hour + 1 >= max_hold_hr:
                return True

        elif campaign == CampaignPhase.C2B:
            soft = self._configured_endpoint(campaign, 'soft_endpoint')
            min_hold_hr = self._float(
                ovr.get('min_hold_hr'),
                self._endpoint_float(campaign, soft, 'min_hold_hr'),
            )
            threshold_kg_hr = self._float(
                ovr.get('threshold_kg_hr'),
                self._endpoint_float(campaign, soft, 'threshold_kg_hr'),
            )
            max_hold_hr = self._max_hold_hr(campaign)
            species = str(soft.get('species', ''))
            if not species:
                raise ValueError('C2B.soft_endpoint.species is required')
            rate = evap_flux.species_kg_hr.get(species, 0.0)
            if melt.campaign_hour >= min_hold_hr and rate < threshold_kg_hr:
                return True
            if melt.campaign_hour >= max_hold_hr:
                return True

        elif campaign == CampaignPhase.C3_K:
            if record.path == 'A_staged':
                staged_hours = int(self._float(
                    self._campaign_overrides(campaign).get('staged_duration_h'),
                    self._configured_max_hold_hr(campaign, 'C3_K', 'A_staged'),
                ))
                if melt.campaign_hour >= max(1, staged_hours):
                    return True
            path_key = 'A' if record.path == 'A' else 'default'
            max_hold_hr = self._configured_max_hold_hr(
                campaign, 'C3_K', path_key)
            if melt.campaign_hour >= max_hold_hr:
                return True

        elif campaign == CampaignPhase.C3_NA:
            # Autoreview r6 P2 (2026-05-27): the V1c-recipe-retune
            # migration retargeted ``C2A_STAGED -> C3_NA`` (was
            # ``C2A_STAGED -> C3_K`` pre-V1c) and the
            # ``na_shuttle_stage`` override now sets
            # ``staged_duration_h`` as the cool cleanup endpoint.  The
            # C3_K branch above honors the override via the
            # ``record.path == 'A_staged'`` check; this branch did not,
            # so staged runs (``record.path == 'A_staged'``) fell into
            # the ``else`` arm of the ternary and ran C3_NA for the
            # default 35 hours instead of the intended ~3-hour cool
            # cleanup.  Mirror the C3_K handling so the staged endpoint
            # is honored at its configured value.
            if record.path == 'A_staged':
                staged_hours = int(self._float(
                    self._campaign_overrides(campaign).get('staged_duration_h'),
                    self._configured_max_hold_hr(campaign, 'C3_NA', 'A_staged'),
                ))
                if melt.campaign_hour >= max(1, staged_hours):
                    return True
            path_key = 'A' if record.path == 'A' else 'default'
            max_hold_hr = self._configured_max_hold_hr(
                campaign, 'C3_NA', path_key)
            if melt.campaign_hour >= max_hold_hr:
                return True

        elif campaign == CampaignPhase.C4:
            soft = self._configured_endpoint(campaign, 'soft_endpoint')
            min_hold_hr = self._float(
                ovr.get('min_hold_hr'),
                self._endpoint_float(campaign, soft, 'min_hold_hr'),
            )
            threshold_kg_hr = self._endpoint_float(
                campaign, soft, 'threshold_kg_hr')
            max_hold_hr = self._max_hold_hr(campaign)
            species = str(soft.get('species', ''))
            if not species:
                raise ValueError('C4.soft_endpoint.species is required')
            rate = evap_flux.species_kg_hr.get(species, 0.0)
            if melt.campaign_hour >= min_hold_hr and rate < threshold_kg_hr:
                return True
            if melt.campaign_hour >= max_hold_hr:
                return True

        elif campaign == CampaignPhase.C5:
            endpoint = self._configured_endpoint(campaign, 'endpoint')
            if record.branch == 'two':
                branch_cfg = self._campaign_config(campaign).get('branch_two', {})
                default_cap_V = self._float(branch_cfg.get('max_voltage_V'), 1.6)
                max_hold_hr = self._configured_max_hold_hr(
                    campaign, 'branch_two')
            else:
                branch_cfg = self._campaign_config(campaign).get('branch_one', {})
                default_cap_V = self._float(branch_cfg.get('max_voltage_V'), 2.5)
                max_hold_hr = self._configured_max_hold_hr(
                    campaign, 'branch_one')
            configured_cap_V = (
                mre_ladder.coerce_mre_decomposition_voltage(
                    getattr(melt, 'mre_max_voltage_V', 0.0)
                )
                or 0.0
            )
            voltage_cap_V = (
                configured_cap_V if configured_cap_V > 0.0 else default_cap_V
            )
            at_cap_margin_V = self._float(
                endpoint.get('at_voltage_margin_V'),
                mre_ladder.C5_DEPLETION_AT_CAP_MARGIN_V,
            )
            threshold_A = self._float(
                endpoint.get('threshold_A'),
                mre_ladder.C5_DEPLETION_LOW_CURRENT_A,
            )
            consecutive_hours = int(
                self._float(
                    endpoint.get('consecutive_hours'),
                    mre_ladder.C5_DEPLETION_CONSECUTIVE_HOURS,
                )
            )
            at_cap = melt.mre_voltage_V >= (voltage_cap_V - at_cap_margin_V)
            if at_cap and melt.mre_current_A < threshold_A:
                melt.mre_low_current_hours += 1
            else:
                melt.mre_low_current_hours = 0
            if melt.mre_low_current_hours >= consecutive_hours:
                return True
            if melt.campaign_hour >= max_hold_hr:
                return True

        elif campaign == CampaignPhase.C6:
            composition_endpoint = self._configured_endpoint(
                campaign, 'composition_endpoint')
            min_hold_hr = self._float(ovr.get('min_hold_hr'), 0.0)
            species = composition_endpoint.get('species', [])
            if not isinstance(species, list) or not species:
                raise ValueError(
                    'C6.composition_endpoint.species must be a list')
            threshold_wt_pct = self._endpoint_float(
                campaign, composition_endpoint, 'threshold_wt_pct')
            max_hold_hr = self._max_hold_hr(campaign)
            comp = melt.composition_wt_pct()
            refractory_pct = sum(comp.get(str(name), 0.0) for name in species)
            if melt.campaign_hour >= min_hold_hr and refractory_pct < threshold_wt_pct:
                return True
            if melt.campaign_hour >= max_hold_hr:
                return True

        elif campaign == CampaignPhase.C7_CA_ALUMINOTHERMIC:
            max_hold_hr = self._max_hold_hr(campaign)
            if melt.campaign_hour >= max_hold_hr:
                return True

        elif campaign == CampaignPhase.MRE_BASELINE:
            soft = self._configured_endpoint(campaign, 'soft_endpoint')
            min_voltage_V = self._endpoint_float(
                campaign, soft, 'min_voltage_V')
            threshold_A = self._endpoint_float(campaign, soft, 'threshold_A')
            consecutive_hours = int(self._endpoint_float(
                campaign, soft, 'consecutive_hours'))
            max_hold_hr = self._configured_max_hold_hr(campaign)
            if (melt.mre_voltage_V >= min_voltage_V
                    and melt.mre_current_A < threshold_A):
                melt.mre_low_current_hours += 1
            else:
                melt.mre_low_current_hours = 0
            if melt.mre_low_current_hours >= consecutive_hours:
                return True
            if melt.campaign_hour >= max_hold_hr:
                return True

        return False

    # ------------------------------------------------------------------
    # Campaign transitions
    # ------------------------------------------------------------------

    def _get_next_after_c5(
        self,
        record: BatchRecord,
    ) -> Optional[CampaignPhase]:
        if record.branch == 'two':
            if self._is_noninteractive_test_batch(record):
                self._record_auto_decision(
                    record, DecisionType.C6_PROCEED, 'yes')
                return CampaignPhase.C6
            return None
        return CampaignPhase.COMPLETE

    @staticmethod
    def _truthy_config(value) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {'1', 'true', 'yes', 'on'}
        return bool(value)

    def _c7_enabled(self, record: BatchRecord) -> bool:
        if getattr(record, 'branch', '') != 'two':
            return False
        cfg = self._campaign_config(CampaignPhase.C7_CA_ALUMINOTHERMIC)
        ovr = self._campaign_overrides(CampaignPhase.C7_CA_ALUMINOTHERMIC)
        enabled = self._truthy_config(ovr.get('enabled', cfg.get('enabled', False)))
        if not enabled:
            return False
        c4_cfg = self._campaign_config(CampaignPhase.C4)
        ca_harvest = c4_cfg.get('optional_Ca_harvest', {})
        if isinstance(ca_harvest, Mapping) and self._truthy_config(
            ca_harvest.get('enabled', False)
        ):
            raise ValueError('c4_ca_harvest_conflicts_with_c7')
        return True

    def get_next_campaign(self, current: CampaignPhase,
                          record: BatchRecord) -> Optional[CampaignPhase]:
        """
        Determine the next campaign after the current one ends.

        Returns:
            CampaignPhase for the next campaign,
            CampaignPhase.COMPLETE if the batch is done,
            or None if a decision is needed first.
        """
        # --- MRE-only track: skip pyrolysis campaigns entirely ---
        # After C0 (degas), go straight to MRE_BASELINE. No C0b, no decisions.
        if record.track == 'mre_baseline':
            if current in (CampaignPhase.C0, CampaignPhase.C0B):
                return CampaignPhase.MRE_BASELINE
            elif current == CampaignPhase.MRE_BASELINE:
                return CampaignPhase.COMPLETE
            else:
                return CampaignPhase.COMPLETE

        # --- Pyrolysis track ---
        if current == CampaignPhase.C0:
            # Check if P-cleanup is needed
            # For simplicity, always do C0b for lunar feedstocks
            return CampaignPhase.C0B

        elif current == CampaignPhase.C0B:
            # Seal volatiles train gate valve
            # Decision needed: Path A or B
            if self._is_noninteractive_test_batch(record):
                record.path = 'A_staged'
                self._record_auto_decision(record, DecisionType.PATH_AB, 'A_staged')
                return CampaignPhase.C2A_STAGED
            return None  # Triggers PATH_AB decision

        elif current == CampaignPhase.C2A:
            # After Path A C2A → C3 (K phase)
            return CampaignPhase.C3_K

        elif current == CampaignPhase.C2A_STAGED:
            # Staged Path A cools before handing residual FeO to the V1c
            # Na-only cleanup window. K/FeO is refused at this temperature.
            scoped = self._c2a_staged_c3_na_scoped_overrides()
            self._pending_c3_na_scoped_overrides = scoped or None
            record.path = 'A_staged'
            return CampaignPhase.C3_NA

        elif current == CampaignPhase.C2B:
            # After Path B C2B → C3 (K phase)
            return CampaignPhase.C3_K

        elif current == CampaignPhase.C3_K:
            if record.path == 'A_staged':
                return CampaignPhase.COMPLETE
            # K phase → Na phase
            return CampaignPhase.C3_NA

        elif current == CampaignPhase.C3_NA:
            # After C3 → Branch decision needed
            if self._is_noninteractive_test_batch(record):
                record.branch = 'two'
                self._record_auto_decision(
                    record, DecisionType.BRANCH_ONE_TWO, 'two')
                return CampaignPhase.C4
            return None  # Triggers BRANCH_ONE_TWO decision

        elif current == CampaignPhase.C4:
            if self.c5_enabled:
                return CampaignPhase.C5
            return self._get_next_after_c5(record)

        elif current == CampaignPhase.C5:
            return self._get_next_after_c5(record)

        elif current == CampaignPhase.C6:
            if self._c7_enabled(record):
                self._record_auto_decision(record, DecisionType.C7_PROCEED, 'yes')
                return CampaignPhase.C7_CA_ALUMINOTHERMIC
            return CampaignPhase.COMPLETE

        elif current == CampaignPhase.C7_CA_ALUMINOTHERMIC:
            return CampaignPhase.COMPLETE

        elif current == CampaignPhase.MRE_BASELINE:
            return CampaignPhase.COMPLETE

        return CampaignPhase.COMPLETE

    def get_decision(self, current: CampaignPhase,
                     record: BatchRecord) -> DecisionPoint:
        """
        Build a DecisionPoint for the operator when a decision is needed.
        """
        if current == CampaignPhase.C0B:
            return DecisionPoint(
                decision_type=DecisionType.PATH_AB,
                options=['A', 'A_staged', 'B'],
                recommendation='A_staged',
                context=(
                    'Path A: Continuous pN₂ ramp extracts Na/K/Fe/SiO₂. '
                    'Path A_staged: staged pN₂ ramp separates alkali, '
                    'SiO, hot Fe hold, then cool Na cleanup. '
                    'Path B: pO₂-managed Fe-only pyrolysis preserving '
                    'CMAS glass for tapping as Material 1.'
                ),
            )

        elif current == CampaignPhase.C3_NA:
            branch_two_context = (
                'Branch Two (preferred): C4 Mg pyrolysis + C6 Mg thermite.'
            )
            if self.c5_enabled:
                branch_two_context = (
                    'Branch Two (preferred): C4 Mg pyrolysis + C5 limited MRE '
                    '+ C6 Mg thermite. MRE ≤1.6 V, ~1200-2000 kWh/t, '
                    'electrode life 5-10x.'
                )
            branch_one_context = (
                'Branch One (fallback): skip C4 and complete pyrolysis-only '
                'when optional C5/MRE is disabled.'
            )
            if self.c5_enabled:
                branch_one_context = (
                    'Branch One (fallback): skip C4, full MRE to 2.5 V, '
                    '~2650-4050 kWh/t, electrode life 2-3x.'
                )
            return DecisionPoint(
                decision_type=DecisionType.BRANCH_ONE_TWO,
                options=['two', 'one'],
                recommendation='two',
                context=(
                    f'{branch_two_context} {branch_one_context}'
                ),
            )

        elif (
            current == CampaignPhase.C5
            or (
                current == CampaignPhase.C4
                and not self.c5_enabled
                and record.branch == 'two'
            )
        ):
            return DecisionPoint(
                decision_type=DecisionType.C6_PROCEED,
                options=['yes', 'no'],
                recommendation='yes',
                context=(
                    'Proceed with C6 Mg thermite reduction? '
                    'Requires ~50-60 kg Mg from inventory. '
                    'Produces Al (+ Ti alloy if TiO₂ retained).'
                ),
            )

        elif current == CampaignPhase.C6 and self._c7_enabled(record):
            return DecisionPoint(
                decision_type=DecisionType.C7_PROCEED,
                options=['yes', 'no'],
                recommendation='yes',
                context=(
                    'Proceed with default-off C7 aluminothermic Ca recovery? '
                    'Requires Al budget, hard vacuum, and a dedicated Ca condenser.'
                ),
            )

        # Fallback
        return DecisionPoint(
            decision_type=DecisionType.ROOT_BRANCH,
            options=['pyrolysis', 'mre_baseline'],
            recommendation='pyrolysis',
            context='Select processing track.',
        )
