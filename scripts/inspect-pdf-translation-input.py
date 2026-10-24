#!/usr/bin/env python3
"""Inspect selected translation inputs; no translation API calls.

The normal local parser/layout/formula pipeline runs until the translation
boundary, where a capture callback raises StopCapture before any LLM request.
Only paragraphs matching --match are written. Cached model/font assets are
copied to a temporary cache; source PDFs and the user cache are unchanged.
"""
import argparse
from dataclasses import asdict, fields
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))


class StopCapture(Exception):
    pass


def describe_style(style):
    """Record useful scalar fields without traversing parser graphics objects."""
    if style is None:
        return None
    graphic_state = getattr(style, 'graphic_state', None)
    instructions = getattr(graphic_state, 'passthrough_per_char_instruction', None)
    if instructions is not None and not isinstance(instructions, (str, int, float, bool)):
        instructions = '<' + type(instructions).__name__ + '>'
    return dict(font_id=style.font_id, font_size=style.font_size,
                graphic_state=dict(passthrough_per_char_instruction=instructions))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pages', default='1')
    parser.add_argument('--index-only', action='store_true',
                        help='Capture only original paragraph text and hash, before preprocessing.')
    parser.add_argument('--match', default=r'Advancing|Bowen|Ning Ding|Received|Accepted|Available online')
    args = parser.parse_args()
    source = args.input.resolve()
    target = args.output.resolve()
    if not target.is_relative_to(ROOT / 'validation'):
        parser.error('Output must be inside this checkout validation directory.')
    matcher = re.compile(args.match, re.I)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pagebridge-input-inspect-') as temporary:
        temporary = Path(temporary)
        cache = temporary / 'cache'
        assets = Path.home() / '.cache/babeldoc'
        cache.mkdir()
        for name in ('fonts', 'models', 'tiktoken'):
            shutil.copytree(assets / name, cache / name)
        import babeldoc.const
        babeldoc.const.CACHE_FOLDER = cache
        babeldoc.const.TIKTOKEN_CACHE_FOLDER = cache / 'tiktoken'
        os.environ['TIKTOKEN_CACHE_DIR'] = str(cache / 'tiktoken')
        import worker
        import layout_guard
        # pdf2zh initializes its own SQLite cache on import. Redirect only the
        # Python path lookup during that import; never change the process HOME.
        with patch.object(Path, 'home', return_value=temporary / 'home'):
            from pdf2zh_next.high_level import create_babeldoc_config
            from pdf2zh_next.translator.base_translator import BaseTranslator
        from babeldoc.format.pdf.high_level import translate
        from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator, ParagraphTranslateTracker
        from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly
        from babeldoc.format.pdf.document_il.utils.formular_helper import is_formulas_font
        layout_guard.install()
        request = dict(sourceLanguage='en', targetLanguage='zh', pages=args.pages, service=dict(
            kind='ollama', baseURL='http://127.0.0.1:9/v1', model='no-network-inspection',
            apiKey='unused', timeoutSeconds=10, concurrency=1))
        settings = worker.build_settings(request, temporary / 'output')
        captured = []
        before_layout_guard = []
        original_preserve = layout_guard.preserve_toc_leaders
        def capture_layout(finder, page):
            if args.index_only:
                return original_preserve(finder, page)
            paragraphs = []
            for p in page.pdf_paragraph:
                if not matcher.search(p.unicode or ''):
                    continue
                compositions = []
                for comp in p.pdf_paragraph_composition:
                    values = {field.name: getattr(comp, field.name) for field in fields(comp)
                              if getattr(comp, field.name) is not None}
                    entries = []
                    for key, value in values.items():
                        chars = [value] if key == 'pdf_character' else getattr(value, 'pdf_character', [])
                        entries.append(dict(type=key, text=''.join(c.char_unicode for c in chars),
                            chars=[[c.char_unicode,c.pdf_character_id,c.box.x,c.box.y,c.box.x2,c.box.y2,
                                    c.pdf_style.font_id,c.pdf_style.font_size] for c in chars]))
                    compositions.extend(entries)
                paragraphs.append(dict(text=p.unicode, label=p.layout_label,
                    box=asdict(p.box), compositions=compositions))
            before_layout_guard.append(dict(page=page.page_number + 1, paragraphs=paragraphs))
            return original_preserve(finder, page)
        def no_network(*a, **kw):
            raise RuntimeError('Network access disabled by inspection script')
        class InspectionTranslator(BaseTranslator):
            name = 'input_inspection'
            def __init__(self):
                self.lang_in, self.lang_out = 'en', 'zh'
            def do_translate(self, text, rate_limit_params=None):
                raise RuntimeError('Translation forbidden during inspection')
            def do_llm_translate(self, text, rate_limit_params=None):
                if text is not None:
                    raise RuntimeError('Translation forbidden during inspection')
                return None
        with patch('pdf2zh_next.high_level.get_translator', return_value=InspectionTranslator()), \
             patch('pdf2zh_next.high_level.get_term_translator', return_value=None), \
             patch.object(socket.socket, 'connect', no_network), \
             patch.object(socket, 'create_connection', no_network):
            config = create_babeldoc_config(settings, source)
        def inspect(self, docs):
            il = self.il_translator if hasattr(self, 'il_translator') else self
            for page in docs.page:
                fonts = {f.font_id: f for f in page.pdf_font}
                xobj_fonts = {obj.xobj_id: {**fonts, **{f.font_id: f for f in obj.pdf_font}}
                              for obj in page.pdf_xobject}
                for paragraph in page.pdf_paragraph:
                    if not matcher.search(paragraph.unicode or ''):
                        continue
                    if args.index_only:
                        source_text = paragraph.unicode
                        captured.append(dict(page=page.page_number + 1,
                            layoutLabel=paragraph.layout_label, paragraphText=source_text,
                            sourceParagraphSHA256=hashlib.sha256(source_text.encode('utf-8')).hexdigest()))
                        continue
                    text, ti = il.pre_translate_paragraph(paragraph, ParagraphTranslateTracker(), fonts, xobj_fonts)
                    used_fonts = xobj_fonts.get(paragraph.xobj_id, fonts)
                    placeholders = []
                    if ti is not None:
                        for placeholder in ti.placeholders:
                            item = placeholder.to_dict()
                            chars = (placeholder.formula.pdf_character if hasattr(placeholder, 'formula')
                                     else placeholder.composition.pdf_character)
                            item['characters'] = [dict(text=c.char_unicode, glyph=c.pdf_character_id,
                                fontId=c.pdf_style.font_id, fontName=used_fonts[c.pdf_style.font_id].name,
                                fontIsFormula=is_formulas_font(used_fonts[c.pdf_style.font_id].name, config.formular_font_pattern),
                                fontSize=c.pdf_style.font_size, box=asdict(c.box),
                                visualBox=asdict(c.visual_bbox.box), formulaLayoutId=c.formula_layout_id)
                                for c in chars]
                            placeholders.append(item)
                    captured.append(dict(page=page.page_number + 1, layoutLabel=paragraph.layout_label,
                        paragraphText=paragraph.unicode, box=asdict(paragraph.box),
                        paragraphStyle=describe_style(paragraph.pdf_style),
                        paragraphFontName=(used_fonts[paragraph.pdf_style.font_id].name
                                           if paragraph.pdf_style is not None else None),
                        baseStyle=describe_style(ti.base_style) if ti is not None else None,
                        translationInput=text, placeholders=placeholders,
                        skipped=ti is None))
            target.write_text(json.dumps(dict(source=str(source), sourceSHA256=source_hash,
                pages=args.pages, networkDisabled=True, translationRequests=0,
                shortLineSplitFactor=config.short_line_split_factor,
                disableRichText=config.disable_rich_text_translate,
                match=args.match, beforeLayoutGuard=before_layout_guard,
                captured=captured), ensure_ascii=False, indent=2) + '\n')
            raise StopCapture()
        logging.getLogger().setLevel(logging.ERROR)
        with patch.object(ILTranslatorLLMOnly, 'translate', inspect), \
             patch.object(ILTranslator, 'translate', inspect), \
             patch.object(layout_guard, 'preserve_toc_leaders', capture_layout), \
             patch.object(socket.socket, 'connect', no_network), \
             patch.object(socket, 'create_connection', no_network):
            try:
                translate(config)
            except StopCapture:
                pass
        assert captured, 'No matching paragraph was captured'
        assert source_hash == hashlib.sha256(source.read_bytes()).hexdigest()
        print(json.dumps(dict(report=str(target), paragraphs=len(captured), translationRequests=0)))


if __name__ == '__main__':
    main()
