"""Build-time snapshots keep maintained resources in one source location."""
from pathlib import Path
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithResources(build_py):
    def run(self):
        super().run()
        root = Path(__file__).parent
        destination = Path(self.build_lib) / 'ifs_pipeline/resources'
        for folder, patterns in {
            'workflows': ('*.md', '*.json', '*.csv'),
            'docs': ('*.md',),
            'source-guides': ('*.md',),
            'reference/datagator': ('*.json', '*.md'),
            'recipes': ('*.template.json',),
        }.items():
            for pattern in patterns:
                for source in (root / folder).rglob(pattern):
                    target = destination / source.relative_to(root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)


setup(cmdclass={'build_py': BuildWithResources})
