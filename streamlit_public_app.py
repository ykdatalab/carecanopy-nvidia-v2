"""Public demo entry point for hosted deployment (e.g. Streamlit Community Cloud).

Forces CARECANOPY_PUBLIC_DEMO=1 before any app module is imported, so the hosted app always
runs offline scripted replay only, even when no secrets are configured. No credentials are needed.
"""
import os
import runpy
from pathlib import Path

os.environ['CARECANOPY_PUBLIC_DEMO'] = '1'
runpy.run_path(str(Path(__file__).resolve().with_name('streamlit_app.py')), run_name='__main__')
