"""Render the actual DOCX locally when the bundled LibreOffice is unavailable.

Aspose is loaded from a temporary package directory; only the PDF conversion
backend changes. The skill's renderer still creates page-N.png for review.
"""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from functools import partial

ROOT = Path(__file__).resolve().parents[1]
TEMP_PACKAGES = Path(tempfile.gettempdir()) / 'impact-docx-render-20261009'
sys.path.insert(0, str(TEMP_PACKAGES))
import aspose.words as aw

SKILL_RENDERER = Path.home() / '.codex/plugins/cache/openai-primary-runtime/documents/26.1007.11041/skills/documents/render_docx.py'
POPPLER = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin'
spec = importlib.util.spec_from_file_location('skill_docx_renderer', SKILL_RENDERER)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)

def convert_with_aspose(doc_path, user_profile, convert_tmp_dir, stem, verbose):
    output = str(Path(convert_tmp_dir) / f'{stem}.pdf')
    source = aw.Document(doc_path)
    source.save(output)
    print('Pages du moteur de rendu :', source.page_count)
    return output, 'Local Aspose DOCX layout engine, evaluation PDF for QA only'

renderer.convert_to_pdf = convert_with_aspose
renderer.convert_from_path = partial(renderer.convert_from_path, poppler_path=str(POPPLER))
sys.argv = [str(SKILL_RENDERER),
            str(ROOT / 'outputs/Comparatif_IMPACT_OpenAI_Ollama_4000_exposants.docx'),
            '--output_dir', str(ROOT / 'outputs/qa/comparatif/pages'),
            '--emit_pdf']
renderer.main()
