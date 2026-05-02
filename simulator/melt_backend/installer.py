"""
AlphaMELTS Auto-Installer
===========================

Detects platform, downloads the alphaMELTS binary from the
magmasource/alphaMELTS GitHub releases, and installs it into
the project's engines/alphamelts/ directory.

Also checks for and optionally installs PetThermoTools and
VapoRock via pip.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Dict, Optional
from urllib.request import urlretrieve


# alphaMELTS download URLs by platform
ALPHAMELTS_VERSION = '2.3.1'
ALPHAMELTS_BASE_URL = (
    'https://magmasource.caltech.edu/alphamelts/zipfiles/'
)

PLATFORM_FILES = {
    ('Darwin', 'x86_64'):  f'alphamelts{ALPHAMELTS_VERSION}_macOS_intel.zip',
    ('Darwin', 'arm64'):   f'alphamelts{ALPHAMELTS_VERSION}_macOS_arm.zip',
    ('Linux', 'x86_64'):   f'alphamelts{ALPHAMELTS_VERSION}_linux64.zip',
    ('Windows', 'AMD64'):  f'alphamelts{ALPHAMELTS_VERSION}_win64.zip',
}


class EngineInstaller:
    """
    Manages installation of thermodynamic engine dependencies.
    """

    def __init__(self):
        self.project_root = Path(__file__).parent.parent.parent
        self.engines_dir = self.project_root / 'engines'
        self.alphamelts_dir = self.engines_dir / 'alphamelts'

    def check_status(self) -> Dict[str, bool]:
        """
        Check availability of all thermodynamic dependencies.

        Returns dict of component → installed (bool).
        """
        status = {}

        # PetThermoTools (Python API for alphaMELTS)
        try:
            import PetThermoTools  # noqa: F401
            status['PetThermoTools'] = True
        except ImportError:
            status['PetThermoTools'] = False

        # VapoRock (vapor pressures)
        try:
            import VapoRock  # noqa: F401
            status['VapoRock'] = True
        except ImportError:
            status['VapoRock'] = False

        # alphaMELTS binary
        status['alphaMELTS_binary'] = self._check_binary()

        # ChemApp / FactSAGE
        try:
            import ChemApp  # noqa: F401
            status['FactSAGE'] = True
        except ImportError:
            status['FactSAGE'] = False

        return status

    def _check_binary(self) -> bool:
        """Check if alphaMELTS binary exists in engines/ or on PATH."""
        # Check project directory
        for name in ('run_alphamelts.command', 'alphamelts', 'alphamelts.exe'):
            if (self.alphamelts_dir / name).exists():
                return True

        # Check system PATH
        return shutil.which('alphamelts') is not None

    def install_alphamelts(self, progress_callback=None) -> bool:
        """
        Download and install alphaMELTS binary.

        Downloads the appropriate platform binary from the
        magmasource Caltech server and extracts it to
        engines/alphamelts/.

        Args:
            progress_callback: Optional callable(message: str)
                               for progress updates

        Returns:
            True if installation succeeded
        """
        def report(msg):
            if progress_callback:
                progress_callback(msg)

        # Detect platform
        system = platform.system()
        machine = platform.machine()
        key = (system, machine)

        filename = PLATFORM_FILES.get(key)
        if filename is None:
            report(f'Unsupported platform: {system} {machine}')
            return False

        url = ALPHAMELTS_BASE_URL + filename

        # Create engines directory
        self.alphamelts_dir.mkdir(parents=True, exist_ok=True)

        # Download
        zip_path = self.alphamelts_dir / filename
        report(f'Downloading {filename}...')
        try:
            urlretrieve(url, zip_path)
        except Exception as e:
            report(f'Download failed: {e}')
            return False

        # Extract
        report('Extracting...')
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(self.alphamelts_dir)
        except Exception as e:
            report(f'Extraction failed: {e}')
            return False

        # Clean up zip
        zip_path.unlink(missing_ok=True)

        # Make executable on Unix
        if system != 'Windows':
            for f in self.alphamelts_dir.iterdir():
                if f.suffix in ('.command', '') and f.is_file():
                    f.chmod(f.stat().st_mode | 0o755)

        # Verify
        if self._check_binary():
            report('alphaMELTS installed successfully.')
            return True
        else:
            report('Installation completed but binary not found.')
            return False

    def install_python_packages(self, packages: list = None,
                                 progress_callback=None) -> Dict[str, bool]:
        """
        Install Python packages via pip.

        Args:
            packages: List of package names. Default: PetThermoTools, VapoRock
            progress_callback: Optional progress reporter

        Returns:
            Dict of package → success
        """
        if packages is None:
            packages = ['PetThermoTools', 'VapoRock']

        def report(msg):
            if progress_callback:
                progress_callback(msg)

        results = {}
        for pkg in packages:
            report(f'Installing {pkg}...')
            try:
                subprocess.check_call(
                    [sys.executable, '-m', 'pip', 'install', pkg],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=120,
                )
                results[pkg] = True
                report(f'{pkg} installed.')
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                results[pkg] = False
                report(f'{pkg} installation failed: {e}')

        return results

    def get_recommendation(self) -> str:
        """
        Return a human-readable summary of what's available
        and what should be installed.
        """
        status = self.check_status()
        lines = ['Thermodynamic Engine Status:']

        if status.get('PetThermoTools'):
            lines.append('  PetThermoTools: installed (Python API mode)')
        else:
            lines.append('  PetThermoTools: not found')

        if status.get('VapoRock'):
            lines.append('  VapoRock: installed (vapor pressure calculations)')
        else:
            lines.append('  VapoRock: not found (using Antoine fallback)')

        if status.get('alphaMELTS_binary'):
            lines.append('  alphaMELTS binary: found')
        else:
            lines.append('  alphaMELTS binary: not found')

        if status.get('FactSAGE'):
            lines.append('  FactSAGE/ChemApp: found')
        else:
            lines.append('  FactSAGE/ChemApp: not available')

        if not any(status.values()):
            lines.append('')
            lines.append('  No thermodynamic engines available.')
            lines.append('  Running with Antoine vapor pressure fallback.')
            lines.append('  For best results, install PetThermoTools:')
            lines.append('    pip install PetThermoTools')

        return '\n'.join(lines)
