"""Seed an isolated SQL consumer fixture before opening the API adapter."""
import os,shutil,subprocess
from pathlib import Path


def seed_frozen_database(environment):
    root=Path(__file__).resolve().parents[2]
    node=shutil.which('node')
    if not node:
        node=str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')
    configured={**os.environ,**environment,'PLATESG_AUTO_PUBLISH':'0'}
    configured.pop('PLATESG_DATABASE_URL',None)
    configured.pop('ESG_DATABASE_URL',None)
    subprocess.run([node,str(root/'PlatESG/tests/fixtures/frozen_database.mjs')],env=configured,check=True,stdout=subprocess.DEVNULL,timeout=120)


def initialize_empty_database(environment):
    root=Path(__file__).resolve().parents[2]
    configured={**os.environ,**environment,'PLATESG_AUTO_PUBLISH':'0'}
    configured.pop('ESG_DATABASE_URL',None)
    configured.pop('PLATESG_DATABASE_URL',None)
    subprocess.run([shutil.which('node'),str(root/'database/runtime/manage.mjs'),'init','--profile','frozen_publication'],env=configured,check=True,stdout=subprocess.DEVNULL,timeout=120)
