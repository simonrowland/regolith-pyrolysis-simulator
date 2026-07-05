"""
Data Persistence
=================

Save/load custom feedstock compositions and test run histories
to local YAML files.  Single-user, file-based storage.

Files:
    data/custom_compositions.yaml — user-created feedstock compositions
    data/test_runs.yaml           — saved simulation run history

LEGACY BOUNDARY (R-F7): this is the single-user, file-based UI store for custom
feedstocks + manual run history. It is NOT the recipe optimizer's run cache. The
optimizer's content-addressed run store is the separate ``results_store.py``
(Phase O / O-P2b3-4: sqlite/WAL keyed by the EvalSpec SHA-256, which includes the
feedstock_recipe_digest so a feedstock composition edit invalidates only that
feedstock's cache). Do not extend this module for optimizer use.
"""

from __future__ import annotations

import datetime
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from simulator.core import BatchRecord, CampaignPhase


DATA_DIR = Path(__file__).parent.parent / 'data'


class RunHistory:
    """Save and load simulation run records."""

    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.file = data_dir / 'test_runs.yaml'

    def _load_all(self) -> dict:
        if not self.file.exists():
            return {'runs': []}
        with open(self.file) as f:
            data = yaml.safe_load(f) or {}
        if 'runs' not in data:
            data['runs'] = []
        return data

    def _save_all(self, data: dict):
        with open(self.file, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def save_run(self, record: BatchRecord) -> str:
        """
        Save a batch record summary to history.

        Returns the generated batch_id.
        """
        batch_id = record.batch_id or str(uuid.uuid4())[:8]
        record.batch_id = batch_id

        data = self._load_all()

        summary = {
            'batch_id': batch_id,
            'feedstock': record.feedstock_key,
            'feedstock_label': record.feedstock_label,
            'batch_mass_kg': record.batch_mass_kg,
            'track': record.track,
            'path': record.path,
            'branch': record.branch,
            'total_hours': record.total_hours,
            'energy_electrical_plus_evaporation_kWh': round(
                record.energy_electrical_plus_evaporation_kWh, 1),
            'energy_electrical_kWh': round(record.energy_electrical_kWh, 1),
            'energy_evaporation_thermal_kWh': round(
                record.energy_evaporation_thermal_kWh, 1),
            'energy_scope': record.energy_scope,
            'furnace_heat_status': record.furnace_heat_status,
            'energy_latent_kWh': round(record.energy_latent_kWh, 1),
            'energy_dissociation_kWh': round(record.energy_dissociation_kWh, 1),
            'energy_breakdown_kWh': {
                k: round(v, 1) for k, v in record.energy_breakdown_kWh.items()
            },
            'oxygen_total_kg': round(record.oxygen_total_kg, 1),
            'products_kg': {k: round(v, 2)
                           for k, v in record.products_kg.items()},
            'completed': record.completed,
            'saved_at': datetime.datetime.now().isoformat(),
        }

        data['runs'].append(summary)
        self._save_all(data)
        return batch_id

    def list_runs(self) -> List[Dict]:
        """Return list of saved run summaries."""
        data = self._load_all()
        return data.get('runs', [])

    def load_run(self, batch_id: str) -> Optional[Dict]:
        """Load a specific run summary by batch_id."""
        for run in self.list_runs():
            if run.get('batch_id') == batch_id:
                return run
        return None

    def delete_run(self, batch_id: str) -> bool:
        """Remove a run from history."""
        data = self._load_all()
        original_len = len(data['runs'])
        data['runs'] = [r for r in data['runs']
                        if r.get('batch_id') != batch_id]
        if len(data['runs']) < original_len:
            self._save_all(data)
            return True
        return False


class CustomCompositions:
    """Manage user-created feedstock compositions."""

    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.file = data_dir / 'custom_compositions.yaml'

    def _load_all(self) -> dict:
        if not self.file.exists():
            return {}
        with open(self.file) as f:
            data = yaml.safe_load(f) or {}
        return data

    def _save_all(self, data: dict):
        with open(self.file, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def save_composition(self, key: str, label: str,
                          composition_wt_pct: Dict[str, float],
                          notes: str = ''):
        """Save or update a custom feedstock composition."""
        data = self._load_all()
        data[key] = {
            'label': label,
            'source': 'User-created',
            'confidence': 'User',
            'composition_wt_pct': composition_wt_pct,
            'note': notes,
            'created_at': datetime.datetime.now().isoformat(),
        }
        self._save_all(data)

    def load_all(self) -> Dict:
        """Load all custom compositions."""
        return self._load_all()

    def delete_composition(self, key: str) -> bool:
        """Remove a custom composition."""
        data = self._load_all()
        if key in data:
            del data[key]
            self._save_all(data)
            return True
        return False
