"""Preserve verified legacy list markers and unambiguous TOC leader glyphs.

Some PDFs map Webdings CID 31 (a filled square) to U+001F. BabelDOC then
strips it as whitespace before looking for list boundaries. Normalize only
that exact font/CID/control-character combination; retain its source glyph,
font, color, geometry and render order. The semantic bullet is recognized as
both a list boundary and a protected formula object by BabelDOC 0.6.2.
"""
from __future__ import annotations

from collections import Counter
import copy
import math
import re
import threading
import unicodedata

GUARD_VERSION = 21
_lock = threading.RLock()
_installed = False
_original_process_page = None
_original_is_formulas_font = None
_original_process_translatable_formulas = None
_original_is_translatable_formula = None
_original_process_comma_formulas = None
_original_should_split_formula = None
_original_styles_process_page = None
_original_layout_typesetting_units = None
_original_find_optimal_scale_and_layout = None
# Formula objects cannot be weak-referenced. Keep the exact source identity for
# this single-document worker lifetime; never infer a binding from target text.
_script_bindings = {}
_hanging_indents = {}
_rejoined_source_paragraphs = {}
_paired_source_paragraphs = {}
_source_overprint_words = {}
_source_accented_names = {}
_formula_context = threading.local()
_counters = Counter()
_TOC_TAIL = re.compile(r"\.{12,}[ \t]*([0-9]{1,4})[ \t]*$")
# Legacy mathematical encodings have misleading ToUnicode mappings (a minus
# can decode as '{'). Preserve their original glyphs, not guessed Unicode.
# Exact, audited font names only: similar AdvP body/italic fonts are excluded.
_LEGACY_MATH_FONTS = frozenset({
    'AdvP40798', 'AdvP497E2', 'AdvP497E3', 'AdvP497E4', 'AdvP497E5',
    'AdvP43B38B', 'AdvP4C4E51', 'AdvP4C4E46', 'AdvP3EAA99', 'AdvP4068C',
    'AdvPi1', 'AdvPi2', 'AdvP7DA6', 'AdvP4980C',
    'AdvP7DB7', 'AdvP697C', 'AdvP43A417', 'AdvP4383EF', 'AdvP44E6F4', 'AdvP7CA8',
})
# These two fonts encode real decimal digits. Upstream may translate standalone
# ordinary numbers; encoded mathematical symbols that happen to look numeric
# in ToUnicode must not take that path (AdvP7DA6 '2' is a minus glyph).
_LEGACY_SYMBOL_FONTS = _LEGACY_MATH_FONTS - {'AdvP497E2', 'AdvP497E4'}


def _composition_characters(composition):
    if composition.pdf_character is not None:
        return [composition.pdf_character]
    for name in ('pdf_line', 'pdf_same_style_characters', 'pdf_formula'):
        value = getattr(composition, name, None)
        if value is not None:
            return value.pdf_character
    return []


def _finite_character(character):
    return bool(character.pdf_style and character.box and character.visual_bbox
                and character.visual_bbox.box and not character.vertical
                and isinstance(character.pdf_style.font_size, (int, float))
                and math.isfinite(character.pdf_style.font_size)
                and character.pdf_style.font_size > 0
                and all(isinstance(v, (int, float)) and math.isfinite(v)
                        for box in (character.box, character.visual_bbox.box)
                        for v in (box.x, box.y, box.x2, box.y2)))


def repair_subscript_offsets(page):
    """Keep a source-proven isolated variable's small subscript below its base.

    Upstream's 0.6 vertical-IoU test can miss a genuinely lowered corner glyph,
    especially when a body-sized space expands its visual formula box. Relocate
    uses that box as its origin, so zero offset raises the glyph above new text.
    Derive the offset from the original base baseline; never shift source glyphs
    or use a fixed font descent. Compound/ambiguous formulas and superscripts
    remain under upstream control.
    """
    fonts = {f.font_id: f for f in page.pdf_font}
    xfonts = {o.xobj_id: {**fonts, **{f.font_id: f for f in o.pdf_font}}
              for o in page.pdf_xobject}
    repaired = 0
    for paragraph in page.pdf_paragraph:
        if paragraph.vertical:
            continue
        lookup = xfonts.get(paragraph.xobj_id, fonts)
        def family(character):
            font = lookup.get(character.pdf_style.font_id) if character.pdf_style else None
            name = getattr(font, 'name', None)
            return re.sub(r'^[A-Z]{6}\+', '', name) if isinstance(name, str) and name else None
        entries = [(c, comp.pdf_formula is not None)
                   for comp in paragraph.pdf_paragraph_composition
                   for c in _composition_characters(comp)]
        for composition in paragraph.pdf_paragraph_composition:
            formula = composition.pdf_formula
            if (formula is None or not formula.box or formula.pdf_curve or formula.pdf_form
                    or formula.is_corner_mark is False
                    or any(not _finite_character(c) or not isinstance(c.char_unicode, str)
                           or not c.char_unicode for c in formula.pdf_character)
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                           for v in (formula.box.x, formula.box.y, formula.box.x2, formula.box.y2))):
                continue
            visible = [c for c in formula.pdf_character if c.char_unicode and c.char_unicode.strip()]
            if len(visible) != 1 or not re.fullmatch(r'[A-Za-z0-9]', visible[0].char_unicode):
                continue
            script = visible[0]
            formula_character_ids = {id(c) for c in formula.pdf_character}
            index = next((i for i, (c, _) in enumerate(entries) if c is script), None)
            if index is None:
                continue
            preceding = [(c, is_formula) for c, is_formula in entries[:index]
                         if id(c) not in formula_character_ids]
            if not preceding:
                continue
            base, base_is_formula = preceding[-1]
            if (base_is_formula or not _finite_character(base)
                    or not re.fullmatch(r'[A-Za-z]', base.char_unicode or '')
                    or len(preceding) > 1 and re.search(r'[A-Za-z0-9_]$', preceding[-2][0].char_unicode or '')):
                continue
            size = base.pdf_style.font_size
            source_family = family(base)
            if (not source_family or family(script) != source_family
                    or any(c.xobj_id != base.xobj_id for c in formula.pdf_character)
                    or base.xobj_id != paragraph.xobj_id
                    or not 0.35 * size <= script.pdf_style.font_size < 0.85 * size
                    or not -0.65 * size <= script.box.y - base.box.y <= -0.12 * size
                    or not -0.15 * size <= script.box.x - base.box.x2 <= 0.6 * size):
                continue
            candidates = [c for position, (c, is_formula) in enumerate(entries)
                          if not is_formula and _finite_character(c)
                          and re.fullmatch(r'[A-Za-z]', c.char_unicode or '')
                          and (position == 0 or not re.search(
                              r'[A-Za-z0-9_]$', entries[position - 1][0].char_unicode or ''))
                          and c.xobj_id == base.xobj_id and family(c) == source_family
                          and abs(c.pdf_style.font_size - size) <= 0.02
                          and -0.15 * size <= script.box.x - c.box.x2 <= 0.6 * size
                          and -0.65 * size <= script.box.y - c.box.y <= -0.12 * size]
            if len(candidates) != 1 or candidates[0] is not base:
                continue
            offset = formula.box.y - base.box.y
            if formula.y_offset is not None and abs(formula.y_offset - offset) <= 0.05:
                continue
            formula.y_offset = offset
            repaired += 1
    if repaired:
        with _lock:
            _counters['repairedSubscriptOffsets'] += repaired
    return repaired


def _guard_styles_process_page(self, page):
    split_source_physical_lines(self, page)
    result = _original_styles_process_page(self, page)
    bind_source_accented_names(self, page)
    restore_source_reference_articles(self, page)
    bind_source_overprinted_words(self, page)
    bind_source_subscript_indices(self, page)
    repair_subscript_offsets(page)
    repair_script_following_formula_offsets(page)
    register_script_bindings(page)
    rejoin_source_column_sentence_tails(self, page)
    rejoin_source_column_orphan_heads(self, page)
    merge_source_bullet_prepositions(self, page)
    merge_source_open_grammar_blocks(self, page)
    merge_source_numbered_rule_titles(self, page)
    rejoin_source_parenthetical_comparisons(self, page)
    rejoin_source_column_short_heads(self, page)
    rejoin_source_column_url_paths(self, page)
    join_source_column_sentence_pairs(self, page)
    return result


def source_sentence_was_rejoined(paragraph):
    """Identity marker for the translation guard's no-silent-skip check."""
    return _rejoined_source_paragraphs.get(id(paragraph)) is paragraph


_LEXICAL_ACCENTS = frozenset({'´','¨','\u0301','\u0308','\u0307'})


def _latin_letter(value):
    return bool(len(value or '') == 1 and value.isalpha()
                and 'LATIN' in unicodedata.name(value,''))

def _native_accent(comp):
    formula = comp.pdf_formula
    if (formula is None or formula.pdf_curve or formula.pdf_form
            or len(formula.pdf_character) != 1):
        return None
    char = formula.pdf_character[0]
    return char if char.char_unicode in _LEXICAL_ACCENTS and _finite_character(char) else None

def _replace_native_glyph_span(styler,comps,start,end,replacement):
    """Replace only the original span's containers; never clone its glyphs."""
    result=[];offset=0
    for comp in comps:
        native=_composition_characters(comp);stop=offset+len(native)
        if stop<=start or offset>=end:
            result.append(comp)
        else:
            if offset<start:
                result.append(styler._create_same_style_composition(
                    native[:start-offset],comp.pdf_same_style_characters.pdf_style))
            if offset<=start<stop:
                result.append(replacement)
            if stop>end:
                result.append(styler._create_same_style_composition(
                    native[end-offset:],comp.pdf_same_style_characters.pdf_style))
        offset=stop
    return result

def bind_source_accented_names(styler, page):
    """Preserve an attested accented surname as one original-glyph unit.

    Only author-list names or capitalized hyphenated names qualify. A lexical
    accent must overlap one same-face baseline vowel; a true raised script,
    formula, ordinary space, or ambiguous base makes the candidate ineligible.
    No combining character, letter or parser space is created or removed.
    """
    from babeldoc.format.pdf.document_il import PdfFormula, PdfParagraphComposition
    repaired = 0
    for paragraph in page.pdf_paragraph:
        if paragraph.vertical or paragraph.layout_label in {'formula','isolate_formula','equation','code'}:
            continue
        comps = paragraph.pdf_paragraph_composition
        chars = [c for comp in comps for c in _composition_characters(comp)]
        owners = [comp for comp in comps for _ in _composition_characters(comp)]
        if not chars or len(set(map(id,chars))) != len(chars):
            continue
        raw = ''.join(c.char_unicode or '' for c in chars)
        author_list = bool(re.match(r'^[^\W\d_][^,]{1,40},\s*(?:[A-Z]\.\s*){1,4}',raw))
        year = re.search(r'\b(?:19|20)\d{2}\.',raw)
        candidates = []
        def lexical(index):
            c, owner = chars[index], owners[index]
            if owner.pdf_same_style_characters is not None and (_latin_letter(c.char_unicode) or c.char_unicode in '-‐‑'):
                return True
            if _native_accent(owner) is c:
                return True
            # The parser may add a blank before an accent which precedes its
            # vowel in stream order. Its measured box overlaps that same vowel;
            # a real word space never satisfies this condition.
            if (owner.pdf_same_style_characters is not None and c.char_unicode == ' '
                    and c.pdf_character_id is None and index+2 < len(chars)
                    and _native_accent(owners[index+1]) is chars[index+1]
                    and _latin_letter(chars[index+2].char_unicode)):
                base = chars[index+2]
                return c.box.x < base.box.x2 and c.box.x2 > base.box.x
            return False
        for at, owner in enumerate(owners):
            if _native_accent(owner) is not chars[at]:
                continue
            start,end = at,at+1
            while start and lexical(start-1):start-=1
            while end < len(chars) and lexical(end):end+=1
            if (start,end) in candidates:
                continue
            selected = chars[start:end]
            letters = [c for c in selected if _latin_letter(c.char_unicode)]
            marks = [c for c in selected if c.char_unicode in _LEXICAL_ACCENTS]
            spelling = ''.join(c.char_unicode for c in selected if c.char_unicode not in _LEXICAL_ACCENTS and not c.char_unicode.isspace())
            parts = re.split('[-‐‑]',spelling)
            proper = (letters and len(letters)>=4 and all(len(s)>=3 and s[0].isupper()
                      and all(_latin_letter(c) for c in s) and any(c.islower()for c in s) for s in parts))
            tail = ''.join(c.char_unicode or '' for c in chars[end:end+12])
            is_author = (author_list and year is not None and sum(len(c.char_unicode or '')for c in chars[:end])<year.start()
                         and re.match(r',\s*(?:[A-Z]\.\s*){1,3}',tail))
            if not proper or not marks or not (len(parts)>=2 or is_author):
                continue
            base = letters[0];size=base.pdf_style.font_size
            normal = [c for c in selected if c.char_unicode not in _LEXICAL_ACCENTS and c.char_unicode.strip()]
            if (any(not _finite_character(c) or c.formula_layout_id is not None
                    or (c.xobj_id != paragraph.xobj_id and not (c.char_unicode == ' '
                        and c.pdf_character_id is None and c.xobj_id is None))
                    or not _definition_same_style(c.pdf_style,base.pdf_style)
                    or (c.char_unicode.strip() and c.pdf_character_id is None) for c in selected)
                    or any(abs(c.box.y-base.box.y)>.06*size for c in selected)
                    or any(not -.12*size <= b.box.x-a.box.x2 <= .18*size for a,b in zip(normal,normal[1:]))):
                continue
            attachments = []
            for mark in marks:
                visual=mark.visual_bbox.box;center=(visual.x+visual.x2)/2
                attached=[c for c in letters if c.char_unicode in 'aeiouyAEIOUYı'
                          and c.box.x <= center <= c.box.x2
                          and .45*size <= (visual.y+visual.y2)/2-c.box.y <= 1.1*size]
                if len(attached)!=1:
                    break
                attachments.append(attached[0])
            if len(attachments)!=len(marks):
                continue
            candidates.append((start,end))
        if not candidates:
            continue
        for start,end in sorted(candidates,reverse=True):
            selected=chars[start:end]
            formula=PdfFormula(pdf_character=selected,x_offset=0,y_offset=0,x_advance=0)
            styler.update_formula_data(formula)
            baseline=next(c.box.y for c in selected if _latin_letter(c.char_unicode))
            formula.y_offset=formula.box.y-baseline
            comps=_replace_native_glyph_span(styler,comps,start,end,
                PdfParagraphComposition(pdf_formula=formula))
            # Only this creation site can certify a lexical native name. Keep
            # both identity and a full immutable attribute snapshot; a later
            # formula which merely resembles text must never gain permission.
            _source_accented_names[id(formula)] = (
                page, paragraph, formula, tuple(selected), copy.deepcopy(formula))
            repaired+=1
        paragraph.pdf_paragraph_composition=comps
    if repaired:
        with _lock:_counters['boundSourceAccentedNames']+=repaired
    return repaired

def source_native_accented_names(paragraph):
    """Return only original binder-created groups still owned by their page."""
    result=[]
    for comp in paragraph.pdf_paragraph_composition:
        formula=comp.pdf_formula
        binding=_source_accented_names.get(id(formula))
        if not binding:continue
        page,owner,original,chars,state=binding
        if (owner is not paragraph or formula is not original
                or sum(p is paragraph for p in page.pdf_paragraph)!=1
                or sum(c.pdf_formula is formula for c in paragraph.pdf_paragraph_composition)!=1
                or len(chars)!=len(formula.pdf_character)
                or any(a is not b for a,b in zip(chars,formula.pdf_character))
                or formula!=state):
            continue
        result.append(binding)
    return tuple(result)


def _same_overprinted_letter(chars):
    if len(chars)!=2 or chars[0] is chars[1]:return False
    a,b=chars
    return (all(_finite_character(c) and c.formula_layout_id is None
                and c.pdf_character_id is not None for c in chars)
            and re.fullmatch(r'[A-Za-z]',a.char_unicode or '')
            and a.char_unicode==b.char_unicode and a.pdf_character_id==b.pdf_character_id
            and a.box==b.box and a.visual_bbox==b.visual_bbox
            and a.xobj_id==b.xobj_id and _definition_same_style(a.pdf_style,b.pdf_style))

def _source_quote_pair(paragraph, quote):
    # Original punctuation has a thin, high horizontal ink box. A genuine euro
    # currency glyph has a tall ink body and cannot satisfy this shape proof.
    if not _finite_character(quote) or quote.pdf_character_id is None:return False
    size=quote.pdf_style.font_size;ink=quote.visual_bbox.box
    if not (.15*size <= ink.x2-ink.x <= .4*size and 0 < ink.y2-ink.y <= .15*size
            and .5*size <= ink.y-quote.box.y <= .9*size):return False
    matches=[]
    for co in paragraph.pdf_paragraph_composition:
        f=co.pdf_formula
        if f is None or len(f.pdf_character)!=1 or f.pdf_curve or f.pdf_form:continue
        other=f.pdf_character[0]
        if (other is not quote and _finite_character(other)
                and other.char_unicode==quote.char_unicode and other.pdf_character_id==quote.pdf_character_id
                and other.xobj_id==quote.xobj_id and _definition_same_style(other.pdf_style,quote.pdf_style)
                and abs((other.visual_bbox.box.x2-other.visual_bbox.box.x)-(ink.x2-ink.x))<.02*size
                and abs((other.visual_bbox.box.y2-other.visual_bbox.box.y)-(ink.y2-ink.y))<.02*size):
            matches.append(other)
    return len(matches)==1

def source_native_overprint_words(paragraph):
    binding=_source_overprint_words.get(id(paragraph))
    if not binding or binding[0] is not paragraph:return ()
    if tuple(id(c) for co in paragraph.pdf_paragraph_composition for c in _composition_characters(co))!=binding[1]:return ()
    return binding[2]

def bind_source_overprinted_words(styler,page):
    """Keep native overprints, repairing only proven lexical container edges."""
    from babeldoc.format.pdf.document_il import PdfFormula,PdfParagraphComposition
    plain_page=' '.join(''.join(c.char_unicode or '' for c in co.pdf_same_style_characters.pdf_character)
                       for q in page.pdf_paragraph for co in q.pdf_paragraph_composition if co.pdf_same_style_characters)
    repaired=0
    for paragraph in page.pdf_paragraph:
        if paragraph.vertical or paragraph.layout_label not in {'text','plain text','plain_text'}:continue
        comps=paragraph.pdf_paragraph_composition;bindings=[]
        for index in range(len(comps)-2,-1,-1):
            formula=comps[index].pdf_formula;right=comps[index+1].pdf_same_style_characters
            if formula is None or formula.pdf_curve or formula.pdf_form or right is None:continue
            native=formula.pdf_character;quote=native[0]if len(native)==3 else None
            doubled=native[-2:]
            if not _same_overprinted_letter(doubled) or len(native)not in(2,3):continue
            raw=''.join(c.char_unicode or ''for c in right.pdf_character)
            match=re.match(r'(?P<tail>[a-z][A-Za-z]{0,15})(?=[^A-Za-z]|$)',raw)
            if not match:continue
            tail=right.pdf_character[:len(match['tail'])]
            if ''.join(c.char_unicode or ''for c in tail)!=match['tail']:continue
            lead=doubled[0];size=lead.pdf_style.font_size
            if (any(not _finite_character(c) or c.formula_layout_id is not None or c.pdf_character_id is None
                    or c.xobj_id!=paragraph.xobj_id or abs(c.box.y-lead.box.y)>.03*size
                    or abs(c.pdf_style.font_size-size)>.02
                    or c.pdf_style.graphic_state!=lead.pdf_style.graphic_state for c in doubled+tail)
                    or any(not -.03*size <= b.box.x-a.box.x2 <= .04*size for a,b in zip([lead]+tail,tail))):continue
            word=lead.char_unicode+match['tail']
            if not re.search(r'(?<![A-Za-z])'+re.escape(word)+r'(?![A-Za-z])',plain_page):continue
            if quote is None:
                if not (lead.char_unicode.isupper() and re.fullmatch(r'[A-Z][a-z]+[A-Z]{2,}',word)):continue
                selected=native+tail
                group=PdfFormula(pdf_character=selected,x_offset=0,y_offset=0,x_advance=0)
                styler.update_formula_data(group);group.y_offset=group.box.y-lead.box.y
                start=sum(len(_composition_characters(co))for co in comps[:index])
                comps=_replace_native_glyph_span(styler,comps,start,start+len(selected),PdfParagraphComposition(pdf_formula=group))
            else:
                if (word not in {'we','they','he','she','it'} or not _source_quote_pair(paragraph,quote)
                        or quote.xobj_id!=paragraph.xobj_id or quote.formula_layout_id is not None
                        or not (lead.box.x <= (quote.visual_bbox.box.x+quote.visual_bbox.box.x2)/2 <= lead.box.x2)
                        or not re.match(r'\s+[a-z]{2,}\s+[a-z]{2,}\s+',raw[len(match['tail']):])):continue
                group=PdfFormula(pdf_character=[quote],x_offset=0,y_offset=0,x_advance=0)
                styler.update_formula_data(group);group.y_offset=group.box.y-lead.box.y
                comps[index:index+1]=[PdfParagraphComposition(pdf_formula=group),
                    styler._create_same_style_composition(doubled,lead.pdf_style)]
                bindings.append((lead.char_unicode+word,word))
            repaired+=1
        paragraph.pdf_paragraph_composition=comps
        if bindings:
            _source_overprint_words[id(paragraph)]=(paragraph,
                tuple(id(c)for co in comps for c in _composition_characters(co)),tuple(bindings))
    if repaired:
        with _lock:_counters['repairedSourceOverprintedWords']+=repaired
    return repaired

def restore_source_reference_articles(styler, page):
    """Restore a baseline article A in an explicitly dated reference subtitle."""
    repaired=0
    for paragraph in page.pdf_paragraph:
        source=paragraph.unicode or ''
        if (paragraph.vertical or not re.match(r'^[^\W\d_][^,]{1,40},\s*(?:[A-Z]\.\s*){1,4}',source)
                or not re.search(r'\b(?:19|20)\d{2}\.',source)):
            continue
        comps=paragraph.pdf_paragraph_composition
        for index in range(len(comps)-2,0,-1):
            formula=comps[index].pdf_formula
            left,right=comps[index-1].pdf_same_style_characters,comps[index+1].pdf_same_style_characters
            if (formula is None or formula.pdf_curve or formula.pdf_form or left is None or right is None
                    or not re.fullmatch(r'A\s*',''.join(c.char_unicode or '' for c in formula.pdf_character))
                    or len(formula.pdf_character)>3):
                continue
            left_chars=[]
            for previous in reversed(comps[:index]):
                if previous.pdf_same_style_characters is None:break
                left_chars[:0]=previous.pdf_same_style_characters.pdf_character
            right_chars=[]
            for following_comp in comps[index+1:]:
                if following_comp.pdf_same_style_characters is None:break
                right_chars.extend(following_comp.pdf_same_style_characters.pdf_character)
            prior=''.join(c.char_unicode or '' for c in left_chars)
            following=''.join(c.char_unicode or '' for c in right_chars)
            if not re.search(r':\s*$',prior) or not re.match(r'\s*[a-z]{2,}\s+[a-z]{2,}\s+[a-z]{2,}',following):
                continue
            article=next(c for c in formula.pdf_character if c.char_unicode=='A')
            first=next((c for c in right_chars if (c.char_unicode or '').strip()),None)
            colon=next((c for c in reversed(left_chars)if (c.char_unicode or '').strip()),None)
            if (first is None or colon is None or not all(_finite_character(c)for c in (article,first,colon))
                    or article.pdf_character_id is None or article.formula_layout_id is not None
                    or not _definition_same_style(article.pdf_style,first.pdf_style)
                    or not _definition_same_style(article.pdf_style,colon.pdf_style)
                    or len({article.xobj_id,first.xobj_id,colon.xobj_id,paragraph.xobj_id})!=1
                    or abs(article.box.y-first.box.y)>.05*article.pdf_style.font_size
                    or not 0 <= first.box.x-article.box.x2 <= .8*article.pdf_style.font_size
                    or not -.05*article.pdf_style.font_size <= colon.box.y-article.box.y <= 1.6*article.pdf_style.font_size):
                continue
            replacements=[]
            for char in formula.pdf_character:
                # Preserve even the original parser-space style; the existing
                # whitespace-only rich-style normalization handles its wrapper.
                replacements.append(styler._create_same_style_composition([char],char.pdf_style))
            comps[index:index+1]=replacements
            repaired+=1
    if repaired:
        with _lock:_counters['restoredSourceReferenceArticles']+=repaired
    return repaired


def _source_body_box(paragraph):
    box, style = paragraph.box, paragraph.pdf_style
    if (paragraph.vertical or paragraph.layout_label not in {'text', 'plain text', 'plain_text'}
            or not box or not style or not isinstance(paragraph.unicode, str)
            or len(re.findall(r'[A-Za-z]{2,}', paragraph.unicode)) < 5
            or re.match(r'\s*(?:[•▪■●–—]|\d+[.)]\s|\(?[A-Za-z][.)]\s)', paragraph.unicode)):
        return None
    values = box.x, box.y, box.x2, box.y2, style.font_size
    if (any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)
            or style.font_size <= 0 or box.x2 - box.x < 8 * style.font_size or box.y2 <= box.y):
        return None
    return values


def _source_column_sentence_pairs(page):
    """Require adjacent reading order and the bottom/top of two full columns."""
    def overlap(a, b):
        return max(0, min(a[2], b[2]) - max(a[0], b[0])) / min(a[2]-a[0], b[2]-b[0])

    bodies = [(p, _source_body_box(p)) for p in page.pdf_paragraph]
    eligible = [(p, g) for p, g in bodies if g]
    for (left, a), (right, b) in zip(bodies, bodies[1:]):
        if (not a or not b or left.xobj_id != right.xobj_id
                or right.first_line_indent is not False
                or left.pdf_style.font_id != right.pdf_style.font_id
                or abs(a[4] - b[4]) > .02
                or not re.match(r'[a-z][a-z]+\b', right.unicode.lstrip())):
            continue
        size = max(a[4], b[4])
        if (not .5 * size <= b[0]-a[2] <= 8 * size
                or not .85 <= (a[2]-a[0])/(b[2]-b[0]) <= 1.18):
            continue
        lc = [(p, g) for p, g in eligible if overlap(a, g) >= .8]
        rc = [(p, g) for p, g in eligible if overlap(b, g) >= .8]
        if (any(overlap(a, g) >= .2 and overlap(b, g) >= .2 for _, g in eligible)
                or any(g[1] < a[1] - .3*size for p, g in lc if p is not left)
                or any(g[3] > b[3] + .3*size for p, g in rc if p is not right)):
            continue
        lt, lb = max(g[3] for _, g in lc), min(g[1] for _, g in lc)
        rt, rb = max(g[3] for _, g in rc), min(g[1] for _, g in rc)
        if abs(lt-rt) <= 2*size and abs(lb-rb) <= 3*size and min(lt-lb, rt-rb) >= 8*size:
            yield left, right


def rejoin_source_column_sentence_tails(styler, page):
    """Move an unambiguous short unfinished prose sentence into its continuation.

    Only the last same-baseline, ordinary-style source suffix is rehomed. The
    target paragraph boxes/styles and every glyph remain untouched; translated
    Unicode is laid out inside the original right-column box. Keeping the
    original glyph coordinates also preserves an entirely untranslated page.
    No formula/citation/script, arbitrary sentence, or cross-page join qualifies.
    """
    from babeldoc.format.pdf.document_il import PdfParagraphComposition
    from babeldoc.format.pdf.document_il.utils.layout_helper import is_same_style

    config = getattr(styler, 'translation_config', None)
    if config is None or getattr(config, 'skip_translation', False):
        return 0
    minimum = getattr(config, 'min_text_length', 0)
    repaired = 0
    for left, right in _source_column_sentence_pairs(page):
        match = re.search(r'[.!?]\s+([A-Z][a-z]+(?:[ ,]+[a-z]+){1,7})\s*$', left.unicode)
        if not match or not left.debug_id or not right.debug_id:
            continue
        tail = match[1]
        retained = left.unicode[:match.start(1)].rstrip()
        if (len(tail) > 64 or tail.split()[-1] not in {'of', 'for', 'to', 'with', 'in', 'on', 'at', 'from', 'by', 'and', 'or'}
                or len(retained) < minimum or len(right.unicode) < minimum
                or len(re.findall(r'[A-Za-z]{2,}', retained)) < 5
                or re.search(r'\b[A-Za-z]{1,3}\.$', retained)
                or any(retained.count(a) != retained.count(b) for a,b in ('()', '[]', '{}'))
                or not left.pdf_paragraph_composition or not right.pdf_paragraph_composition):
            continue
        # The last plain-style composition must contain the source sentence
        # boundary. Never split a formula or reconstruct an ambiguous glyph run.
        comp = left.pdf_paragraph_composition[-1]
        run = comp.pdf_same_style_characters
        first = right.pdf_paragraph_composition[0].pdf_same_style_characters
        if not run or not first or not first.pdf_character:
            continue
        raw = ''.join(c.char_unicode or '' for c in run.pdf_character)
        suffix = re.search(r'([.!?])\s*(' + re.escape(tail).replace(r'\ ', r'\s*') + r')\s*$', raw)
        all_chars = [c for part in left.pdf_paragraph_composition for c in _composition_characters(part)]
        if (not suffix or ''.join(left.unicode.split()) != ''.join(''.join(c.char_unicode or '' for c in all_chars).split())
                or not is_same_style(left.pdf_style, right.pdf_style)
                or not is_same_style(run.pdf_style, right.pdf_style)
                or not is_same_style(first.pdf_style, right.pdf_style)):
            continue
        # The cut must coincide with an actual glyph boundary (no ligature cut).
        cut, offset = None, 0
        for index, char in enumerate(run.pdf_character):
            if offset == suffix.start(2):
                cut = index
                break
            offset += len(char.char_unicode or '')
        if cut is None or cut == 0:
            continue
        moved = run.pdf_character[cut:]
        prior = run.pdf_character[cut-1]
        next_char = first.pdf_character[0]
        size = right.pdf_style.font_size
        baseline = moved[0].box.y if moved[0].box else None
        if (baseline is None or not _finite_character(prior) or not _finite_character(next_char)
                or next_char.xobj_id != right.xobj_id or next_char.formula_layout_id is not None
                or not is_same_style(next_char.pdf_style, right.pdf_style)
                or any(not _finite_character(c) or c.formula_layout_id is not None
                or not re.fullmatch(r'[A-Za-z, ]+', c.char_unicode or '')
                or not is_same_style(c.pdf_style, right.pdf_style)
                or (c.xobj_id != left.xobj_id and not (c.xobj_id is None and c.pdf_character_id is None and c.char_unicode == ' '))
                or abs(c.box.y-baseline) > .1*size for c in moved)
                or abs(prior.box.y-baseline) > .1*size
                or any(not -.1*size <= b.box.x-a.box.x2 <= 1.2*size for a,b in zip(moved, moved[1:]))
                or abs(moved[-1].box.x2-left.box.x2) > size
                or abs(next_char.box.x-right.box.x) > .5*size
                or abs(next_char.box.y2-right.box.y2) > .5*size):
            continue
        kept_run, moved_run = copy.copy(run), copy.copy(run)
        kept_run.pdf_character, moved_run.pdf_character = run.pdf_character[:cut], moved
        # Container boxes describe their own source glyphs; paragraph boxes are
        # deliberately not recomputed, otherwise this would span both columns.
        for value in (kept_run, moved_run):
            value.box = copy.copy(run.box)
            if value.box:
                value.box.x = min(c.box.x for c in value.pdf_character)
                value.box.y = min(c.box.y for c in value.pdf_character)
                value.box.x2 = max(c.box.x2 for c in value.pdf_character)
                value.box.y2 = max(c.box.y2 for c in value.pdf_character)
        left.pdf_paragraph_composition[-1] = PdfParagraphComposition(pdf_same_style_characters=kept_run)
        right.pdf_paragraph_composition.insert(0, PdfParagraphComposition(pdf_same_style_characters=moved_run))
        left.unicode, right.unicode = retained, tail + ' ' + right.unicode.lstrip()
        _rejoined_source_paragraphs[id(left)] = left
        _rejoined_source_paragraphs[id(right)] = right
        repaired += 1
    if repaired:
        with _lock:
            _counters['rejoinedSourceColumnSentenceTails'] += repaired
    return repaired


def rejoin_source_column_orphan_heads(styler, page):
    """Complete an explicit ``both ...s.`` across two source columns.

    Move only the short, ordinary source noun ending the sentence, retaining
    its glyphs and both paragraph boxes. A footer may occur between the body
    paragraphs in the parser stream only when it lies wholly below them.
    This is not a general cross-column sentence merger.
    """
    from babeldoc.format.pdf.document_il.utils.layout_helper import is_same_style

    config = getattr(styler, 'translation_config', None)
    if config is None or getattr(config, 'skip_translation', False):
        return 0
    minimum = getattr(config, 'min_text_length', 0)
    def same_body_style(x, y):
        if (not x or not y or x.font_id != y.font_id
                or abs(x.font_size-y.font_size) > .02):
            return False
        if is_same_style(x,y):
            return True
        # Some PDFs vary an unused stroke width between filled black text
        # runs. No other differing color/graphics operator qualifies here.
        def black_fill(style):
            commands = getattr(style.graphic_state, 'passthrough_per_char_instruction', '') or ''
            return bool(re.search(r'(?<!\S)0 0 0 (?:rg|sc)(?!\S)', commands)
                        and re.fullmatch(r'(?:(?:0 0 0 (?:rg|RG|sc)|[0-9]+(?:\.[0-9]+)? w)\s*)+', commands))
        return black_fill(x) and black_fill(y)
    repaired = 0
    paragraphs = page.pdf_paragraph
    for index, left in enumerate(paragraphs):
        a = _source_body_box(left)
        if not a or not re.search(r'\bboth\s*$', left.unicode or ''):
            continue
        following = index + 1
        while (following < len(paragraphs)
               and paragraphs[following].layout_label == 'abandon'
               and paragraphs[following].box
               and paragraphs[following].box.y2 < a[1]):
            following += 1
        if following >= len(paragraphs):
            continue
        right = paragraphs[following]
        b = _source_body_box(right)
        match = re.match(r'(?P<head>[a-z]{2,25}(?:\s+[a-z]{2,25}){0,2}s)\.\s+(?=[A-Z])',
                         right.unicode or '')
        if (not b or not match or not left.debug_id or not right.debug_id
                or right.first_line_indent is not False
                or left.xobj_id != right.xobj_id
                or not same_body_style(left.pdf_style, right.pdf_style)):
            continue
        size = a[4]
        if (not .5*size <= b[0]-a[2] <= 8*size
                or not .85 <= (a[2]-a[0])/(b[2]-b[0]) <= 1.18
                or abs(a[1]-b[1]) > 3*size
                or b[3]-b[1] < 8*size
                or len(right.unicode[match.end():]) < minimum):
            continue
        # Prove bottom-left to top-right among same-style body paragraphs;
        # full-width abstracts in another font are not part of either column.
        same = [(p, _source_body_box(p)) for p in paragraphs
                if _source_body_box(p) and same_body_style(p.pdf_style, left.pdf_style)]
        def overlaps(g, box):
            return max(0, min(g[2], box[2])-max(g[0], box[0])) / min(g[2]-g[0], box[2]-box[0])
        lc = [g for p,g in same if overlaps(g,a) >= .8]
        rc = [g for p,g in same if overlaps(g,b) >= .8]
        if (any(overlaps(g,a) >= .2 and overlaps(g,b) >= .2 for p,g in same)
                or any(g[1] < a[1]-.3*size for g in lc)
                or any(g[3] > b[3]+.3*size for g in rc)
                or abs(max(g[3] for g in lc)-b[3]) > 3*size):
            continue
        if not left.pdf_paragraph_composition or not right.pdf_paragraph_composition:
            continue
        last = left.pdf_paragraph_composition[-1].pdf_same_style_characters
        first = right.pdf_paragraph_composition[0].pdf_same_style_characters
        if not last or not first or not last.pdf_character or not first.pdf_character:
            continue
        if (not same_body_style(last.pdf_style, left.pdf_style)
                or not same_body_style(first.pdf_style, left.pdf_style)
                or not re.search(r'\bboth\s*$', ''.join(c.char_unicode or '' for c in last.pdf_character))):
            continue
        raw = ''.join(c.char_unicode or '' for c in first.pdf_character)
        if raw != match['head'] + '.' or len(right.pdf_paragraph_composition) < 3:
            continue
        # Reuse the existing separating space, rather than inventing a source
        # glyph. Space-only style runs can use a different PDF font/size.
        separator_comp = right.pdf_paragraph_composition[1]
        separator = separator_comp.pdf_same_style_characters
        if (not separator or not separator.pdf_character
                or any(not c.char_unicode or c.char_unicode.strip()
                       or not _finite_character(c) or c.formula_layout_id is not None
                       for c in separator.pdf_character)):
            continue
        moved = first.pdf_character
        visible = [c for c in moved if c.char_unicode.strip()]
        if (not visible or any(not _finite_character(c) or c.formula_layout_id is not None
                or not re.fullmatch(r'[A-Za-z. ]+', c.char_unicode or '')
                or not same_body_style(c.pdf_style, left.pdf_style)
                or (c.xobj_id != right.xobj_id and not (c.xobj_id is None and c.pdf_character_id is None and c.char_unicode == ' '))
                for c in moved)
                or abs(visible[0].box.x-b[0]) > .5*size
                or abs(visible[0].box.y2-b[3]) > .5*size
                or any(abs(c.box.y-visible[0].box.y) > .1*size for c in visible)
                or any(not -.1*size <= y.box.x-x.box.x2 <= 1.2*size for x,y in zip(visible, visible[1:]))):
            continue
        left.pdf_paragraph_composition.extend([separator_comp, right.pdf_paragraph_composition[0]])
        right.pdf_paragraph_composition = right.pdf_paragraph_composition[2:]
        left.unicode = left.unicode.rstrip() + ' ' + match['head'] + '.'
        right.unicode = right.unicode[match.end():].lstrip()
        _rejoined_source_paragraphs[id(left)] = left
        _rejoined_source_paragraphs[id(right)] = right
        repaired += 1
    if repaired:
        with _lock:
            _counters['rejoinedSourceColumnOrphanHeads'] += repaired
    return repaired


def _flow_body_style(a, b):
    """Same source body style, including one narrowly equivalent black fill.

    The final PDF ``0 0 0 rg`` overrides preceding non-stroking colors. Only
    those color operators may differ, with the same named graphics state.
    No opacity, clipping, transformation, stroke, or text operator is ignored.
    """
    if (not a or not b or a.font_id != b.font_id
            or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v <= 0
                   for v in (a.font_size,b.font_size))
            or abs(a.font_size-b.font_size) > .02):
        return False
    def graphics(style):
        value=getattr(style.graphic_state,'passthrough_per_char_instruction',None)
        if value is None:return ''
        if isinstance(value,str):return value
        # Lazy PDF state caches on rendering/equality. Inspect an independent
        # copy so proving source equality does not mutate any native glyph.
        from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import LazyPassthroughInstruction,PassthroughSnapshot
        if not isinstance(value,LazyPassthroughInstruction):return None
        if value._value is not None:return value._value
        clone=copy.copy(value)
        if isinstance(value.snapshot,PassthroughSnapshot):
            # Snapshot nodes are immutable; only its render cache is mutable.
            clone.snapshot=copy.copy(value.snapshot);clone.snapshot._render_cache={}
        elif not isinstance(value.snapshot,tuple):return None
        return str(clone)
    ga,gb=graphics(a),graphics(b)
    if ga is None or gb is None:return False
    if ga==gb:return True
    def black(value):
        match = re.fullmatch(r'\s*(/[A-Za-z][A-Za-z0-9]* gs)\s+'
            r'(?:(?:/[A-Za-z][A-Za-z0-9]* cs\s+[0-9.]+ scn|0 0 0 0 k)\s+)*0 0 0 rg\s*', value)
        return match[1] if match else None
    return black(ga) is not None and black(ga) == black(gb)


def _flow_ending(text):
    for kind,pattern in (
            ('hyphen',r'\b[a-z]{3,24}-$'),
            ('infinitive',r'\bto$'),
            ('noun',r'\b(?:a|an|the) (?:given|specified|learned|trained|observed|resulting|selected)$')):
        match=re.search(pattern,text or '')
        if match:return kind,match[0]
    return None


def _flow_head_phrase(left, right):
    """Bounded source grammar for a short sentence-final ordinary prose head.

    Complete only a hyphenated word, an infinitive with a definite object,
    or a determiner/adjective noun phrase (including an explicit year-period
    adjunct). This is not a general paragraph/sentence merger.
    """
    l,r=left.unicode or '',right.unicode or '';ending=_flow_ending(l)
    if not ending:return None
    kind=ending[0]
    if kind=='hyphen':
        head=re.match(r'[a-z]{2,24}(?: or [a-z]{2,24}(?: [a-z]{2,24})?)?\.(?=\s+[A-Z])',r)
    elif kind=='infinitive':
        head=re.match(r'(?:form|build|create|produce|obtain|complete|construct|determine|derive|calculate|estimate|describe|represent|generate) the [a-z]{2,24}(?: [a-z]{2,24}){0,2}\.(?=\s+[A-Z])',r)
    else:
        head=re.match(r'(?:[a-z]{3,24}|[A-Z][A-Z0-9]{1,7}, (?:considering|during|over) the [12][0-9]{3}[–-][0-9]{2,4} period)\.(?=\s+[A-Z])',r)
    if (not head or len(head[0])>56 or len(head[0])*4>len(l)
            or any(l.count(x)!=l.count(y)for x,y in ('()','[]','{}'))):return None
    return head[0]


def _flow_text_key(text):
    # Source paragraph normalization may turn a native spacing diaeresis into
    # its combining equivalent. This read-only comparison never changes a
    # glyph or formula; the moved boundary still needs an exact ASCII match.
    return _definition_compact(unicodedata.normalize('NFKD',text or ''))


def _flow_column_pairs(page,minimum_height=4):
    """Adjacent bottom-left/top-right body paragraphs, with actual columns.

    A differently styled full-width title/metadata block entirely above the
    column area is not part of the column flow. A spanning body in the area,
    a later left body, or an earlier right body always blocks the repair.
    """
    bodies=[(p,_source_body_box(p))for p in page.pdf_paragraph]
    def overlap(a,b):return max(0,min(a[2],b[2])-max(a[0],b[0]))/min(a[2]-a[0],b[2]-b[0])
    for (left,a),(right,b) in zip(bodies,bodies[1:]):
        if (not a or not b or left.xobj_id != 0 or right.xobj_id != 0
                or right.first_line_indent is not False
                or not _flow_body_style(left.pdf_style,right.pdf_style)):
            continue
        size=a[4]
        if (not .5*size <= b[0]-a[2] <= 8*size
                or not .85 <= (a[2]-a[0])/(b[2]-b[0]) <= 1.18
                or a[3]-a[1] < minimum_height*size):
            continue
        same=[(p,g)for p,g in bodies if g and _flow_body_style(p.pdf_style,left.pdf_style)]
        ls=[g for p,g in same if overlap(a,g)>=.8]
        rs=[g for p,g in same if overlap(b,g)>=.8]
        if not ls or not rs:continue
        top=max(max(g[3]for g in ls),max(g[3]for g in rs))
        if (any(overlap(a,g)>=.2 and overlap(b,g)>=.2 for p,g in same)
                or abs(max(g[3]for g in ls)-max(g[3]for g in rs))>3*size
                or min(max(g[3]for g in ls)-min(g[1]for g in ls),max(g[3]for g in rs)-min(g[1]for g in rs))<8*size
                or any(g[1]<a[1]-.3*size for p,g in bodies if g and overlap(a,g)>=.8)
                or any(g[3]>b[3]+.3*size for p,g in bodies if g and overlap(b,g)>=.8
                       and not(overlap(a,g)>=.2 and g[1]>top))
                or any(g[1]<top and g[3]>a[1] and overlap(a,g)>=.2 and overlap(b,g)>=.2
                       for p,g in bodies if g)):
            continue
        yield left,right


def _flow_page_pair(left_page,right_page):
    """Adjacent pages, right-column last body followed by left-column first."""
    lb=getattr(getattr(left_page,'mediabox',None),'box',None)
    rb=getattr(getattr(right_page,'mediabox',None),'box',None)
    if (right_page.page_number != left_page.page_number+1 or not lb or lb!=rb
            or any(not isinstance(v,(int,float))or not math.isfinite(v) for v in (lb.x,lb.y,lb.x2,lb.y2))):return None
    ls=[(p,_source_body_box(p))for p in left_page.pdf_paragraph];ls=[(p,b)for p,b in ls if b]
    rs=[(p,_source_body_box(p))for p in right_page.pdf_paragraph];rs=[(p,b)for p,b in rs if b]
    if not ls or not rs:return None
    left,a=ls[-1];right,b=rs[0];size=a[4];width=lb.x2-lb.x
    if (left.xobj_id!=0 or right.xobj_id!=0 or right.first_line_indent is not False
            or not _flow_body_style(left.pdf_style,right.pdf_style)
            or not .85<=(a[2]-a[0])/(b[2]-b[0])<=1.18
            or a[0]<lb.x+.45*width or b[0]>lb.x+.2*width
            or a[1]>lb.y+.15*(lb.y2-lb.y) or b[3]<lb.y+.8*(lb.y2-lb.y)
            or a[3]-a[1]<4*size
            or any(g[1]<a[1]-.2*size for p,g in ls if p is not left)
            or any(g[3]>b[3]+.2*size for p,g in rs if p is not right)
            or not any(g[2]<a[0]-.5*size and abs((g[2]-g[0])-(a[2]-a[0]))<2*size for p,g in ls)
            or not any(g[0]>b[2]+.5*size and abs((g[2]-g[0])-(b[2]-b[0]))<2*size for p,g in rs)):
        return None
    # The exact source resource is required. Page-sampled ascent/descent and
    # character metrics may differ; they are not PDF font resource identity.
    af=[f for f in left_page.pdf_font if f.font_id==left.pdf_style.font_id]
    bf=[f for f in right_page.pdf_font if f.font_id==right.pdf_style.font_id]
    if len(af)!=1 or len(bf)!=1:return None
    names=('font_id','xref_id','name','encoding_length','bold','italic','monospace','serif')
    if (not isinstance(af[0].xref_id,int) or af[0].xref_id<=0
            or any(getattr(af[0],n,None)!=getattr(bf[0],n,None)for n in names)):
        return None
    return left,right


def _rejoin_source_flow_head(styler,left,right):
    """Rehome one source-proven single-line head; never alter native glyphs."""
    config=getattr(styler,'translation_config',None)
    if config is None or getattr(config,'skip_translation',False):return False
    fragment=_flow_head_phrase(left,right)
    if not fragment or not left.debug_id or not right.debug_id:return False
    remainder=right.unicode[len(fragment):].lstrip()
    if (len(remainder)<max(24,getattr(config,'min_text_length',0))
            or len(re.findall(r'[A-Za-z]{2,}',remainder))<5
            or not left.pdf_paragraph_composition or not right.pdf_paragraph_composition):return False
    tail=left.pdf_paragraph_composition[-1].pdf_same_style_characters
    first=right.pdf_paragraph_composition[0].pdf_same_style_characters
    if (not tail or not first or not tail.pdf_character or not first.pdf_character
            or not _flow_body_style(tail.pdf_style,left.pdf_style)
            or not _flow_body_style(first.pdf_style,right.pdf_style)
            or _flow_text_key(''.join(c.char_unicode or''for c in _definition_chars(left)))!=_flow_text_key(left.unicode)
            or _flow_text_key(''.join(c.char_unicode or''for c in _definition_chars(right)))!=_flow_text_key(right.unicode)):
        return False
    # A plain trailing run must prove the same incomplete grammar. Formula
    # tokens or similarly spelled native math glyphs cannot establish a join.
    raw_tail=''.join(c.char_unicode or''for c in tail.pdf_character)
    ending=_flow_ending(left.unicode)[1]
    if not _definition_compact(raw_tail).endswith(_definition_compact(ending)):return False
    moved=[];raw=''
    for c in first.pdf_character:
        if len(_definition_compact(raw))>=len(_definition_compact(fragment)):break
        moved.append(c);raw+=c.char_unicode or''
    kept=first.pdf_character[len(moved):]
    following=next((c for c in kept if(c.char_unicode or'').strip()),None)
    previous=next((c for c in reversed(tail.pdf_character)if(c.char_unicode or'').strip()),None)
    if (not moved or not kept or not previous or not following
            or _definition_compact(raw)!=_definition_compact(fragment)):return False
    size=left.pdf_style.font_size
    visible=[c for c in moved if(c.char_unicode or'').strip()]
    if not visible:return False
    baseline=visible[0].box.y if visible[0].box else None
    def ordinary(c,style):
        return (_finite_character(c) and c.formula_layout_id is None
                and _flow_body_style(c.pdf_style,style)
                and (c.xobj_id==0 and c.pdf_character_id is not None
                     or c.xobj_id is None and c.pdf_character_id is None and c.char_unicode==' '))
    ending_chars=[];ending_raw=''
    for c in reversed(tail.pdf_character):
        if len(_definition_compact(ending_raw))>=len(_definition_compact(ending)):break
        ending_chars.insert(0,c);ending_raw=(c.char_unicode or'')+ending_raw
    if (_definition_compact(ending_raw)!=_definition_compact(ending)
            or any(not ordinary(c,left.pdf_style)for c in ending_chars)):
        return False
    if (baseline is None or not ordinary(previous,left.pdf_style)or not ordinary(following,right.pdf_style)
            or any(not ordinary(c,right.pdf_style) or not re.fullmatch(r'[A-Za-z0-9, .–-]+',c.char_unicode or'')
                   or abs(c.box.y-baseline)>.1*size for c in moved)
            or abs(visible[0].box.x-right.box.x)>.5*size
            or abs(visible[0].box.y2-right.box.y2)>.5*size
            or abs(previous.box.x2-left.box.x2)>size
            or any(not -.1*size<=y.box.x-x.box.x2<=1.2*size for x,y in zip(visible,visible[1:]))):
        return False
    # Retain every existing source space on the right, and keep all coordinates
    # and original objects. Only the ownership of the complete head changes.
    moved_run,kept_run=copy.copy(first),copy.copy(first)
    moved_run.pdf_character=moved;kept_run.pdf_character=kept
    for run in (moved_run,kept_run):
        run.box=copy.copy(first.box)
        if run.box:
            run.box.x=min(c.box.x for c in run.pdf_character);run.box.y=min(c.box.y for c in run.pdf_character)
            run.box.x2=max(c.box.x2 for c in run.pdf_character);run.box.y2=max(c.box.y2 for c in run.pdf_character)
    moved_comp,kept_comp=copy.copy(right.pdf_paragraph_composition[0]),copy.copy(right.pdf_paragraph_composition[0])
    moved_comp.pdf_same_style_characters=moved_run;kept_comp.pdf_same_style_characters=kept_run
    left.pdf_paragraph_composition.append(moved_comp);right.pdf_paragraph_composition[0]=kept_comp
    separator=''if left.unicode.endswith('-')else' '
    left.unicode=left.unicode.rstrip()+separator+fragment;right.unicode=remainder
    _rejoined_source_paragraphs[id(left)]=left;_rejoined_source_paragraphs[id(right)]=right
    return True


def rejoin_source_column_short_heads(styler,page):
    repaired=sum(_rejoin_source_flow_head(styler,l,r)for l,r in _flow_column_pairs(page))
    if repaired:
        with _lock:_counters['rejoinedSourceColumnShortHeads']+=repaired
    return repaired


def rejoin_source_page_short_heads(translator,docs):
    repaired=0
    for left_page,right_page in zip(docs.page,docs.page[1:]):
        pair=_flow_page_pair(left_page,right_page)
        if pair is not None:repaired+=_rejoin_source_flow_head(translator,*pair)
    if repaired:
        with _lock:_counters['rejoinedSourcePageShortHeads']+=repaired
    return repaired


def _paired_source_binding(paragraph):
    record=_paired_source_paragraphs.get(id(paragraph))
    return record if record and record['left']is paragraph else None


def join_source_column_sentence_pairs(styler,page):
    """One pure source sentence, two adjacent two-line column fragments.

    The complete source is translated once. The consumed partner remains as
    an empty reserved target box; the paired typesetter never expands either
    box. This path cannot contain formulas, citations, URLs or rich styles.
    """
    config=getattr(styler,'translation_config',None)
    if config is None or getattr(config,'skip_translation',False):return 0
    repaired=0
    for left,right in _flow_column_pairs(page,minimum_height=1):
        if _paired_source_binding(left)or _paired_source_binding(right):continue
        a,b=left.unicode or'',right.unicode or''
        if (not re.search(r'\b(?:the|this|that|a|an) (?:new|same|other|selected|proposed|original|learned|trained)$',a)
                or not re.match(r'[a-z]{2,24}s\b',b)
                or not re.search(r'\b(?:are|were|is|was)\b',b)
                or not b.endswith('.') or any(ch in a for ch in '.!?')
                or any(ch in b[:-1]for ch in '.!?')
                or any(ch in a+b for ch in '[]{}()<>:/@')
                or not 60<=len(a)<=180 or not 60<=len(b)<=180
                or len(a+b)>340 or len(a+b)<getattr(config,'min_text_length',0)
                or not re.fullmatch(r'[A-Za-z0-9 ,;–-]+',a)
                or not re.fullmatch(r'[A-Za-z0-9 ,;–-]+\.',b)):
            continue
        # Source links were captured after paragraph finding, before Styles.
        # A whole-link association must not expand to newly joined text.
        import pdf_links
        if pdf_links.has_source_links(page,left) or pdf_links.has_source_links(page,right):
            continue
        lc,rc=_plain_source_characters(left),_plain_source_characters(right)
        size=left.pdf_style.font_size
        if (not lc or not rc or not _definition_same_style(left.pdf_style,right.pdf_style)
                or any(not 1.8*size<=p.box.y2-p.box.y<=3*size for p in (left,right))):continue
        def lines(cs):
            ys=[]
            for c in cs:
                if not(c.char_unicode or'').strip():continue
                if not any(abs(c.box.y-y)<=.15*size for y in ys):ys.append(c.box.y)
            return ys
        if len(lines(lc))!=2 or len(lines(rc))!=2:continue
        # No source glyph is created, dropped, edited or duplicated. The same
        # adjacent reading-order sequence becomes one logical paragraph.
        record=dict(left=left,right=right,page=page,boxes=(copy.copy(left.box),copy.copy(right.box)),
                    styles=(copy.copy(left.pdf_style),copy.copy(right.pdf_style)),
                    sourceText=a+' '+b,sourceGlyphs=tuple(lc+rc))
        left.pdf_paragraph_composition.extend(right.pdf_paragraph_composition)
        left.unicode=a.rstrip()+' '+b.lstrip()
        right.pdf_paragraph_composition=[];right.unicode=''
        _paired_source_paragraphs[id(left)]=record
        _rejoined_source_paragraphs[id(left)]=left
        repaired+=1
    if repaired:
        with _lock:_counters['pairedSourceColumnSentences']+=repaired
    return repaired


class _PairedUnitTracker:
    """Keep input-unit indices while using the existing line-break engine."""
    def __init__(self,unit,number,indices):self.unit,self.number,self.indices=unit,number,indices
    def __getattr__(self,name):return getattr(self.unit,name)
    def relocate(self,x,y,scale):
        result=self.unit.relocate(x,y,scale);self.indices[id(result)]=self.number;return result


def _layout_paired_source_units(self,units,record,scale,line_skip,use_english_line_break):
    if not units or any(u.formular or u.char or u.unicode is None for u in units):
        raise ValueError('Paired source flow requires translated plain Unicode units')
    left,right=record['left'],record['right'];boxes=record['boxes']
    if (left.box!=boxes[0] or right.box!=boxes[1]
            or right.pdf_paragraph_composition or right.unicode
            or left.pdf_style!=record['styles'][0] or right.pdf_style!=record['styles'][1]):
        raise ValueError('Paired source target box or ownership changed')
    laid=[];offset=0
    for n,box in enumerate(boxes):
        # Reserve one glyph width for upstream's hanging punctuation; every
        # rendered glyph must still lie within the actual original rectangle.
        inner=copy.copy(box);inner.x2-=max(u.width for u in units[offset:])*scale
        local=copy.copy(left if n==0 else right)
        if n:local.first_line_indent=False
        indices={};tracked=[_PairedUnitTracker(u,i,indices)for i,u in enumerate(units[offset:],offset)]
        output,unused_fit=_original_layout_typesetting_units(self,tracked,inner,scale,line_skip,local,use_english_line_break)
        if not output:return [],False
        accepted=[];next_offset=len(units)
        for unit in output:
            b=unit.box
            if not(box.x-.001<=b.x<=b.x2<=box.x2+.001 and box.y-.001<=b.y<=b.y2<=box.y2+.001):
                next_offset=indices[id(unit)];break
            accepted.append(unit)
        if not accepted:return [],False
        # Current upstream returns all units (including overflow) or [],False.
        # If a compatible backend instead returns only a fitted prefix, the
        # False flag cannot prove which next source unit to flow. Retry scale
        # rather than treating that prefix as a complete output or dropping it.
        if not unused_fit and next_offset==len(units):return [],False
        laid.extend(accepted);offset=next_offset
        if offset==len(units):break
    if offset<len(units):return [],False
    source=''.join(u.unicode for u in units)
    target=''.join(u.unicode for u in laid)
    if _definition_compact(source)!=_definition_compact(target):
        raise ValueError('Paired flow dropped or reordered translated Unicode')
    return laid,True


def _guard_find_optimal_scale_and_layout(self,paragraph,page,typesetting_units,
        initial_scale=1.0,use_english_line_break=True,apply_layout=False):
    record=_paired_source_binding(paragraph)
    if record is None:
        return _original_find_optimal_scale_and_layout(self,paragraph,page,typesetting_units,
            initial_scale,use_english_line_break,apply_layout)
    if record['page']is not page:
        raise ValueError('Paired source paragraph belongs to another page')
    # Never invoke upstream's paragraph expansion or 10% fallback. A complete
    # sentence that cannot fit its two source boxes is an explicit failure.
    floor=max(.75,7.0/paragraph.pdf_style.font_size)
    scales=[min(1.0,max(floor,initial_scale))-i*.025 for i in range(11)]
    for scale in scales:
        if scale<floor-.0001:continue
        laid,fits=_layout_paired_source_units(self,typesetting_units,record,scale,
            1.5 if self.is_cjk else 1.3,use_english_line_break)
        if not fits:continue
        if apply_layout:
            from babeldoc.format.pdf.document_il import PdfParagraphComposition
            rendered=[]
            for unit in laid:
                chars,curves,forms=unit.render()
                if curves or forms or not chars:raise ValueError('Unexpected object in paired plain-text flow')
                for c in chars:
                    b=c.box
                    if not any(box.x-.001<=b.x<=b.x2<=box.x2+.001 and box.y-.001<=b.y<=b.y2<=box.y2+.001 for box in record['boxes']):
                        raise ValueError('Paired translation rendered outside its original boxes')
                    rendered.append(PdfParagraphComposition(pdf_character=c))
            paragraph.pdf_paragraph_composition=rendered;paragraph.scale=scale
        return scale,laid if apply_layout else None
    raise ValueError('Complete translated sentence does not fit its two original boxes at readable size')


def _definition_compact(text):
    return ''.join((text or '').split())


def rejoin_source_column_url_paths(styler, page):
    """Rehome an original URI path split at a proven body-column boundary.

    Only an HTTP(S) URI ending at a slash plus a short native slash-delimited
    path and terminal period qualify. The right paragraph keeps its next full
    sentence and target box. No URI, glyph or clickable annotation is invented.
    """
    config=getattr(styler,'translation_config',None)
    if config is None or getattr(config,'skip_translation',False):return 0
    all_chars=list(page.pdf_character)+[c for p in page.pdf_paragraph for c in _definition_chars(p)]
    counts=Counter(map(id,all_chars));repaired=0
    for left,right in _flow_column_pairs(page):
        uri=re.search(r'\bhttps?://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::[0-9]{2,5})?/(?:[A-Za-z0-9._~-]+/)*$',left.unicode or '')
        path=re.match(r'(?:[A-Za-z0-9_~-]+/){1,8}\.(?=\s+[A-Z])',right.unicode or '')
        if not uri or not path or len(path[0])>64 or len(path[0])*3>len(left.unicode):continue
        rest=right.unicode[path.end():].lstrip()
        if len(rest)<max(24,getattr(config,'min_text_length',0))or len(re.findall(r'[A-Za-z]{2,}',rest))<6:continue
        def ordinary(p):
            if not p.debug_id or len(p.pdf_paragraph_composition)!=1 or any(not co.pdf_same_style_characters for co in p.pdf_paragraph_composition):return None
            cs=_definition_chars(p)
            if (not cs or _definition_compact(''.join(c.char_unicode or''for c in cs))!=_definition_compact(p.unicode)
                    or any(not _finite_character(c)or c.formula_layout_id is not None
                           or not _flow_body_style(c.pdf_style,p.pdf_style)
                           or not isinstance(c.char_unicode,str)
                           or counts[id(c)]!=1
                           or (c.char_unicode.strip()and(c.pdf_character_id is None or c.xobj_id!=0))for c in cs)):
                return None
            return cs
        lc,rc=ordinary(left),ordinary(right)
        if not lc or not rc:continue
        tail=left.pdf_paragraph_composition[-1].pdf_same_style_characters
        first=right.pdf_paragraph_composition[0].pdf_same_style_characters
        if not _definition_same_style(tail.pdf_style,first.pdf_style):continue
        if not _definition_compact(''.join(c.char_unicode or''for c in tail.pdf_character)).endswith(uri[0]):continue
        raw='';moved=[]
        for c in first.pdf_character:
            if len(raw)>=len(path[0]):break
            moved.append(c);raw+=c.char_unicode or''
        kept=first.pdf_character[len(moved):]
        if raw!=path[0]or not kept:continue
        size=left.pdf_style.font_size;baseline=moved[0].box.y
        previous=next((c for c in reversed(lc)if(c.char_unicode or'').strip()),None)
        if (not previous or abs(previous.box.x2-left.box.x2)>size
                or any(abs(c.box.y-baseline)>.1*size for c in moved)
                or abs(moved[0].box.x-right.box.x)>.5*size
                or abs(moved[0].box.y2-right.box.y2)>.5*size
                or any(not (-.2 if b is moved[-1] and b.char_unicode=='.' else -.1)*size
                           <=b.box.x-a.box.x2<=1.2*size
                           or b.box.x<a.box.x or b.box.x2<a.box.x2
                       for a,b in zip(moved,moved[1:]))):continue
        # One native same-style run is retained. Upstream's one-run input
        # uses the proven paragraph Unicode, avoiding a synthetic whitespace
        # at the original column jump inside this single URI.
        tail.pdf_character.extend(moved)
        if tail.box:
            tail.box=copy.copy(tail.box)
            tail.box.x=min(c.box.x for c in tail.pdf_character);tail.box.y=min(c.box.y for c in tail.pdf_character)
            tail.box.x2=max(c.box.x2 for c in tail.pdf_character);tail.box.y2=max(c.box.y2 for c in tail.pdf_character)
        first.pdf_character=kept
        if first.box:
            first.box=copy.copy(first.box)
            first.box.x=min(c.box.x for c in kept);first.box.y=min(c.box.y for c in kept)
            first.box.x2=max(c.box.x2 for c in kept);first.box.y2=max(c.box.y2 for c in kept)
        left.unicode+=path[0];right.unicode=rest
        _rejoined_source_paragraphs[id(left)]=left;_rejoined_source_paragraphs[id(right)]=right
        repaired+=1
    if repaired:
        with _lock:_counters['rejoinedSourceColumnURLPaths']+=repaired
    return repaired


def _definition_same_style(a,b):
    return bool(a and b and a.font_id==b.font_id and a.graphic_state==b.graphic_state
                and isinstance(a.font_size,(int,float)) and isinstance(b.font_size,(int,float))
                and abs(a.font_size-b.font_size)<=.02)


def _definition_chars(paragraph):
    return [c for comp in paragraph.pdf_paragraph_composition
            for c in _composition_characters(comp)]


def _open_grammar_native(paragraph, fonts, *, title, reference=None):
    """Source-native prose with attested equivalent subset faces and fill."""
    if (paragraph.vertical or not paragraph.box or not paragraph.pdf_style
            or not paragraph.pdf_paragraph_composition):
        return None
    reference = reference or paragraph.pdf_style
    def face(style):
        font = fonts.get(style.font_id) if style else None
        if not font or not font.name or font.xref_id is None:
            return None
        return (re.sub(r'^[A-Z]{6}\+', '', font.name), font.bold, font.italic, font.monospace, font.serif)
    expected = face(reference)
    if expected is None:
        return None
    chars = []
    for comp in paragraph.pdf_paragraph_composition:
        if sum(getattr(comp, field, None) is not None for field in (
                'pdf_line', 'pdf_formula', 'pdf_character', 'pdf_same_style_characters',
                'pdf_same_style_unicode_characters')) != 1:
            return None
        if comp.pdf_same_style_characters:
            native = comp.pdf_same_style_characters.pdf_character
        elif (title and comp.pdf_formula is not None and not comp.pdf_formula.pdf_curve
              and not comp.pdf_formula.pdf_form and len(comp.pdf_formula.pdf_character) == 1
              and comp.pdf_formula.pdf_character[0].char_unicode == '+'):
            # A source-written plus inside a proper name remains the identical
            # opaque formula object; do not flatten or recreate its glyph.
            native = comp.pdf_formula.pdf_character
        else:
            return None
        for char in native:
            style = char.pdf_style
            if (not _finite_character(char) or not isinstance(char.char_unicode, str)
                    or char.formula_layout_id is not None
                    or face(style) != expected or style.graphic_state != reference.graphic_state
                    or abs(style.font_size-reference.font_size) > .02
                    or char.xobj_id != paragraph.xobj_id
                    or (char.char_unicode.strip() and char.pdf_character_id is None)):
                return None
        chars.extend(native)
    if (not chars or len(set(map(id,chars))) != len(chars)
            or _definition_compact(''.join(c.char_unicode for c in chars)) != _definition_compact(paragraph.unicode)):
        return None
    return chars


def merge_source_open_grammar_blocks(styler, page):
    """Join same-column native blocks where by/of leaves grammar unfinished.

    Retain every original run, formula and font resource; only paragraph
    ownership and its union target box change. Headings require a short proper
    name continuation; body paragraphs cannot contain any formula composition.
    """
    config = getattr(styler, 'translation_config', None)
    if config is None or getattr(config, 'skip_translation', False):
        return 0
    fonts = {f.font_id:f for f in page.pdf_font}
    if len(fonts) != len(page.pdf_font):
        return 0
    paragraphs = list(page.pdf_paragraph)
    all_chars = list(page.pdf_character) + [c for p in paragraphs for co in p.pdf_paragraph_composition
                                           for c in _composition_characters(co)]
    counts = Counter(map(id,all_chars))
    removed, merged = set(), 0
    for left, right in zip(paragraphs,paragraphs[1:]):
        title = left.layout_label == 'title'
        if (id(left) in removed or left.layout_label != right.layout_label
                or left.layout_label not in {'title','plain text','text'}
                or left.xobj_id != right.xobj_id
                or not re.search(r'\s(?:by|of)$',left.unicode or '')
                or any(token in (left.unicode or '')+(right.unicode or '') for token in ('`','<code','</code'))):
            continue
        if title:
            if (not re.fullmatch(r'[A-Z][A-Za-z]+(?:[ -][A-Za-z]{2,}){0,10} (?:by|of)',left.unicode)
                    or not re.fullmatch(r'[A-Z][A-Za-z]{2,}(?:[ +&-][A-Z][A-Za-z]{2,}){0,7}',right.unicode or '')):
                continue
        elif (len(re.findall(r'[A-Za-z]{2,}',left.unicode)) < 8
              or len(re.findall(r'[A-Za-z]{2,}',right.unicode or '')) < 8):
            continue
        lc = _open_grammar_native(left,fonts,title=title)
        rc = _open_grammar_native(right,fonts,title=title,reference=left.pdf_style)
        if not lc or not rc or any(counts[id(c)] != 1 for c in lc+rc):
            continue
        size = left.pdf_style.font_size
        a,b = left.box,right.box
        if (any(not isinstance(v,(int,float)) or not math.isfinite(v)
                for v in (a.x,a.y,a.x2,a.y2,b.x,b.y,b.x2,b.y2))
                or min(a.x2-a.x,b.x2-b.x,a.y2-a.y,b.y2-b.y) <= 0
                or abs(a.x-b.x) > .2*size or b.y2 >= a.y2
                or (not title and not .8 <= (b.x2-b.x)/(a.x2-a.x) <= 1.2)
                or (title and b.x2-a.x2 > 2*size)):
            continue
        end = next(c for c in reversed(lc) if c.char_unicode.strip())
        start = next(c for c in rc if c.char_unicode.strip())
        if not .9*size <= end.box.y-start.box.y <= 1.6*size:
            continue
        box = copy.copy(a)
        box.x=min(a.x,b.x);box.y=min(a.y,b.y);box.x2=max(a.x2,b.x2);box.y2=max(a.y2,b.y2)
        if (box.y2-box.y > (3.5 if title else 15)*size
                or any(p is not left and p is not right and p.box
                       and min(p.box.x2,box.x2)>max(p.box.x,box.x)
                       and min(p.box.y2,box.y2)>max(p.box.y,box.y) for p in paragraphs)):
            continue
        left.pdf_paragraph_composition = list(left.pdf_paragraph_composition)+list(right.pdf_paragraph_composition)
        left.unicode = left.unicode.rstrip()+' '+right.unicode.lstrip()
        left.box = box
        _rejoined_source_paragraphs[id(left)] = left
        removed.add(id(right));merged += 1
    if removed:
        page.pdf_paragraph = [p for p in page.pdf_paragraph if id(p) not in removed]
        with _lock:
            _counters['mergedSourceOpenGrammarBlocks'] += merged
    return merged


def _plain_source_characters(paragraph):
    """Ordinary source runs only, including already present parser spaces."""
    if (paragraph.vertical or not paragraph.box or not paragraph.pdf_style
            or not paragraph.debug_id or paragraph.xobj_id != 0
            or any(not isinstance(v,(int,float)) or not math.isfinite(v)
                   for v in (paragraph.box.x,paragraph.box.y,paragraph.box.x2,paragraph.box.y2))
            or paragraph.box.x2 <= paragraph.box.x or paragraph.box.y2 <= paragraph.box.y
            or not paragraph.pdf_paragraph_composition
            or any(not c.pdf_same_style_characters for c in paragraph.pdf_paragraph_composition)):
        return None
    characters = _definition_chars(paragraph)
    if (not characters or _definition_compact(paragraph.unicode) != _definition_compact(
            ''.join(c.char_unicode or '' for c in characters))
            or any(not _finite_character(c) or c.formula_layout_id is not None
                   or not _definition_same_style(c.pdf_style, paragraph.pdf_style)
                   or not re.fullmatch(r"[A-Za-z0-9 ,:;.'()\-]+", c.char_unicode or '')
                   or (c.char_unicode.strip() and (c.pdf_character_id is None or c.xobj_id != 0))
                   for c in characters)):
        return None
    return characters


def merge_source_numbered_rule_titles(styler, page):
    """Reunite an explicit numbered Rule heading split into adjacent title boxes.

    A single column, native face/style, aligned starts and regular line spacing
    prove the group. Keep every glyph and use only the original boxes' union.
    No body text or new numbered heading is consumed.
    """
    config = getattr(styler, 'translation_config', None)
    if config is None or getattr(config, 'skip_translation', False):
        return 0
    paragraphs, result, merged, index = page.pdf_paragraph, [], 0, 0
    while index < len(paragraphs):
        first = paragraphs[index]
        if (first.layout_label != 'title'
                or not re.match(r'^Rule\s*\d{1,3}:\s+[A-Z]', first.unicode or '')):
            result.append(first); index += 1; continue
        original = _plain_source_characters(first)
        if not original:
            result.append(first); index += 1; continue
        group, all_chars, following = [first], list(original), index + 1
        size = first.pdf_style.font_size
        while following < len(paragraphs) and len(group) < 4:
            candidate = paragraphs[following]
            native = _plain_source_characters(candidate)
            if (candidate.layout_label != 'title' or not native
                    or re.match(r'^(?:Rule\s*)?\d+[.:)]', candidate.unicode)
                    or not re.match(r'^[A-Z][A-Za-z]', candidate.unicode)
                    or not _definition_same_style(first.pdf_style, candidate.pdf_style)
                    or abs(first.box.x-candidate.box.x) > .15*size
                    or candidate.box.x2-first.box.x2 > 2*size):
                break
            previous_y = next(c.box.y for c in reversed(all_chars) if c.char_unicode.strip())
            next_y = next(c.box.y for c in native if c.char_unicode.strip())
            if not .9*size <= previous_y-next_y <= 1.6*size:
                break
            group.append(candidate); all_chars.extend(native); following += 1
        if len(group) == 1:
            result.append(first); index += 1; continue
        box = copy.copy(first.box)
        box.x=min(p.box.x for p in group); box.y=min(p.box.y for p in group)
        box.x2=max(p.box.x2 for p in group); box.y2=max(p.box.y2 for p in group)
        if (box.y2-box.y > 6*size or box.x2-box.x < 6*size
                or any(p.box and not any(p is item for item in group)
                       and min(p.box.x2,box.x2)>max(p.box.x,box.x)
                       and min(p.box.y2,box.y2)>max(p.box.y,box.y) for p in paragraphs)):
            result.append(first); index += 1; continue
        run = copy.copy(first.pdf_paragraph_composition[0].pdf_same_style_characters)
        run.pdf_character = all_chars; run.box = copy.copy(box)
        composition = copy.copy(first.pdf_paragraph_composition[0])
        composition.pdf_same_style_characters = run
        first.unicode = ' '.join(p.unicode.strip() for p in group)
        first.pdf_paragraph_composition = [composition]; first.box = box
        _rejoined_source_paragraphs[id(first)] = first
        result.append(first); index = following; merged += 1
    if merged:
        page.pdf_paragraph = result
        with _lock: _counters['mergedSourceNumberedRuleTitles'] += merged
    return merged


def rejoin_source_parenthetical_comparisons(styler, page):
    """Complete one ordinary parenthetical comparison across adjacent columns.

    Only a single-line ``than to ...).`` head can move. Same body style proves
    column bounds; differently styled full-width authors/affiliations do not
    define those bounds. The existing general column-pair test is unchanged.
    Both target paragraph boxes stay intact, as do all original glyphs.
    """
    config = getattr(styler, 'translation_config', None)
    if config is None or getattr(config, 'skip_translation', False):return 0
    repaired = 0
    for left, right in zip(page.pdf_paragraph, page.pdf_paragraph[1:]):
        a,b = _source_body_box(left),_source_body_box(right)
        ending = re.search(r'\(or at least (?:(?:much|far) )?more [a-z]{3,24}$',left.unicode or '')
        head = re.match(r'than to [a-z]+(?: [a-z]+){1,10}\)\.(?=\s+[A-Z])',right.unicode or '')
        if not a or not b or not ending or not head:continue
        lc,rc = _plain_source_characters(left),_plain_source_characters(right)
        if (not lc or not rc
                or not _definition_same_style(left.pdf_style,right.pdf_style)
                or right.first_line_indent is not False
                or left.unicode.count('(')-left.unicode.count(')') != 1
                or any(left.unicode[:ending.start()].count(x)!=left.unicode[:ending.start()].count(y)
                       for x,y in ('()','[]','{}'))
                or len(head[0])>80 or len(right.unicode[head.end():].strip())<getattr(config,'min_text_length',5)):
            continue
        size=a[4]
        if not (.5*size <= b[0]-a[2] <= 8*size and .85 <= (a[2]-a[0])/(b[2]-b[0]) <= 1.18):continue
        eligible=[(p,_source_body_box(p))for p in page.pdf_paragraph
                  if _source_body_box(p)and _definition_same_style(p.pdf_style,left.pdf_style)]
        def overlap(x,y):return max(0,min(x[2],y[2])-max(x[0],y[0]))/min(x[2]-x[0],y[2]-y[0])
        ls=[g for p,g in eligible if overlap(a,g)>=.8];rs=[g for p,g in eligible if overlap(b,g)>=.8]
        if (any(overlap(a,g)>=.2 and overlap(b,g)>=.2 for _,g in eligible)
                or any(g[1]<a[1]-.3*size for g in ls)
                or any(g[3]>b[3]+.3*size for g in rs)
                or abs(max(g[3]for g in ls)-max(g[3]for g in rs))>2*size
                or abs(min(g[1]for g in ls)-min(g[1]for g in rs))>3*size
                or min(max(g[3]for g in ls)-min(g[1]for g in ls),max(g[3]for g in rs)-min(g[1]for g in rs))<8*size):continue
        # Do not cross any formula/container or partial native glyph boundary.
        first=right.pdf_paragraph_composition[0].pdf_same_style_characters
        moved=[];raw=''
        for c in first.pdf_character:
            if len(_definition_compact(raw))>=len(_definition_compact(head[0])):break
            moved.append(c);raw+=c.char_unicode or ''
        kept=first.pdf_character[len(moved):]
        if (not moved or not kept or _definition_compact(raw)!=_definition_compact(head[0])
                or any(abs(c.box.y-moved[0].box.y)>.1*size for c in moved)
                or abs(moved[0].box.x-b[0])>.5*size
                or abs(moved[0].box.y2-b[3])>.5*size
                or any(not -.1*size<=y.box.x-x.box.x2<=1.2*size for x,y in zip(moved,moved[1:]))):continue
        moved_run,kept_run=copy.copy(first),copy.copy(first)
        moved_run.pdf_character=moved;kept_run.pdf_character=kept
        for run in (moved_run,kept_run):
            run.box=copy.copy(first.box)
            run.box.x=min(c.box.x for c in run.pdf_character);run.box.y=min(c.box.y for c in run.pdf_character)
            run.box.x2=max(c.box.x2 for c in run.pdf_character);run.box.y2=max(c.box.y2 for c in run.pdf_character)
        moved_comp,kept_comp=copy.copy(right.pdf_paragraph_composition[0]),copy.copy(right.pdf_paragraph_composition[0])
        moved_comp.pdf_same_style_characters=moved_run;kept_comp.pdf_same_style_characters=kept_run
        left.pdf_paragraph_composition.append(moved_comp);right.pdf_paragraph_composition[0]=kept_comp
        left.unicode=left.unicode.rstrip()+' '+head[0];right.unicode=right.unicode[head.end():].lstrip()
        _rejoined_source_paragraphs[id(left)]=left;_rejoined_source_paragraphs[id(right)]=right
        repaired+=1
    if repaired:
        with _lock:_counters['rejoinedSourceParentheticalComparisons']+=repaired
    return repaired


def _research_body_style(a, b):
    """Same body font/size and fill; allow only unused black stroke differences."""
    if (not a or not b or a.font_id != b.font_id
            or any(not isinstance(v, (int,float)) or not math.isfinite(v) or v <= 0 for v in (a.font_size,b.font_size))
            or abs(a.font_size-b.font_size) > .02):
        return False
    if a.graphic_state == b.graphic_state:
        return True
    def black(style):
        commands = getattr(style.graphic_state, 'passthrough_per_char_instruction', '') or ''
        return bool(re.search(r'(?<!\S)0 0 0 (?:rg|sc)(?!\S)', commands)
                    and re.fullmatch(r'(?:(?:0 0 0 (?:rg|RG|sc)|[0-9]+(?:\.[0-9]+)? w)\s*)+', commands))
    return black(a) and black(b)


def rejoin_source_page_research_phrases(translator, docs):
    """Rehome a short list noun phrase split inside ``research and development``.

    Only exact native prose after a comma moves from the last right-column body
    to the first unindented left-column body on the next page. A floating figure
    caption may be above that first body. Paragraph target boxes and all source
    glyph/formula objects remain unchanged; the following list item is untouched.
    """
    config = getattr(translator, 'translation_config', None)
    if config is None or getattr(config, 'skip_translation', False):
        return 0
    minimum = getattr(config, 'min_text_length', 0)
    count = 0
    for lp, rp in zip(docs.page, docs.page[1:]):
        if rp.page_number != lp.page_number+1 or not lp.mediabox or lp.mediabox != rp.mediabox:
            continue
        media = lp.mediabox.box
        if (not media or any(not isinstance(v,(int,float)) or not math.isfinite(v)
                for v in (media.x,media.y,media.x2,media.y2)) or media.x2 <= media.x or media.y2 <= media.y):
            continue
        bodies = [[(p, _source_body_box(p)) for p in page.pdf_paragraph if _source_body_box(p)] for page in (lp, rp)]
        if not all(bodies):
            continue
        left, a = bodies[0][-1]; right, b = bodies[1][0]
        size = a[4]
        ending = re.search(r',\s*(?P<tail>[a-z]{2,20}(?: [a-z]{2,20}){0,3} for (?:[a-z]{2,20} ){0,2}research and)\s*$', left.unicode)
        if (not ending or len(ending['tail']) > 72 or not right.unicode.startswith('development,')
                or right.first_line_indent is not False or left.xobj_id != 0 or right.xobj_id != 0
                or not left.debug_id or not right.debug_id or not _research_body_style(left.pdf_style, right.pdf_style)
                or a[0] < media.x+.45*(media.x2-media.x) or b[0] > media.x+.2*(media.x2-media.x)
                or a[1] > media.y+.15*(media.y2-media.y) or not .85 <= (a[2]-a[0])/(b[2]-b[0]) <= 1.18
                or len(left.unicode[:ending.start('tail')].strip()) < minimum
                or len(right.unicode) < minimum):
            continue
        prefix_text = left.unicode[:ending.start('tail')]
        if any(prefix_text.count(a) != prefix_text.count(b) for a,b in (('(',')'),('[',']'),('{','}'))):
            continue
        if (not any(g[2] < a[0]-.5*size and abs((g[2]-g[0])-(a[2]-a[0])) < 2*size for _,g in bodies[0])
                or not any(g[0] > b[2]+.5*size and abs((g[2]-g[0])-(b[2]-b[0])) < 2*size for _,g in bodies[1])
                or any(g[1] < a[1]-.2*size for p,g in bodies[0] if p is not left)):
            continue
        li = next(i for i,p in enumerate(lp.pdf_paragraph) if p is left)
        ri = next(i for i,p in enumerate(rp.pdf_paragraph) if p is right)
        if any(p.layout_label != 'abandon' for p in lp.pdf_paragraph[li+1:]):
            continue
        # A float cannot hide an intervening body sentence, title, or footnote.
        if any(p.layout_label != 'abandon' and not (p.layout_label == 'figure_caption'
                and p.box and p.box.y > right.box.y2 and p.box.x >= right.box.x-size
                and p.box.x2 <= right.box.x2+size) for p in rp.pdf_paragraph[:ri]):
            continue
        if any(g[3] > b[3]+.2*size and g[0] < b[2] for p,g in bodies[1] if p is not right):
            continue
        # Exact composition boundary: no splitting a glyph/ligature or moving
        # formulas, citations, URLs, brackets, footnote markers, or other items.
        target = _definition_compact(ending['tail']); assembled = ''; cut = len(left.pdf_paragraph_composition)
        while cut and len(assembled) < len(target):
            cut -= 1; comp = left.pdf_paragraph_composition[cut]
            if not comp.pdf_same_style_characters:
                break
            assembled = _definition_compact(''.join(c.char_unicode or '' for c in comp.pdf_same_style_characters.pdf_character)) + assembled
        moved = left.pdf_paragraph_composition[cut:]
        native = [c for comp in moved for c in _composition_characters(comp)]
        visible = [c for c in native if (c.char_unicode or '').strip()]
        first = right.pdf_paragraph_composition[0].pdf_same_style_characters if right.pdf_paragraph_composition else None
        if (assembled != target or not visible or not first or not first.pdf_character
                or not ''.join(c.char_unicode or '' for c in first.pdf_character).startswith('development,')
                or _definition_compact(''.join(c.char_unicode or '' for c in _definition_chars(left))) != _definition_compact(left.unicode)
                or _definition_compact(''.join(c.char_unicode or '' for c in _definition_chars(right))) != _definition_compact(right.unicode)):
            continue
        all_native = _definition_chars(left)+_definition_chars(right)
        if len(set(map(id, all_native))) != len(all_native):
            continue
        baseline = visible[0].box.y if visible[0].box else None
        head = [c for c in first.pdf_character if (c.char_unicode or '').strip()]
        if (baseline is None or any(not _finite_character(c) or c.formula_layout_id is not None
                or c.xobj_id != 0 or c.pdf_character_id is None or not re.fullmatch('[a-z]', c.char_unicode or '')
                or not _research_body_style(c.pdf_style, left.pdf_style) or abs(c.box.y-baseline) > .1*size for c in visible)
                or any(not _finite_character(c) for c in native)
                or any(not -.1*size <= y.box.x-x.box.x2 <= 1.2*size for x,y in zip(visible,visible[1:]))
                or abs(visible[-1].box.x2-left.box.x2) > size
                or not head or any(not _finite_character(c) or c.formula_layout_id is not None or c.xobj_id != 0
                    or not _research_body_style(c.pdf_style,right.pdf_style) for c in head)
                or abs(head[0].box.x-right.box.x) > .3*size or abs(head[0].box.y2-right.box.y2) > .4*size):
            continue
        fa = {f.font_id:f for f in lp.pdf_font}; fb = {f.font_id:f for f in rp.pdf_font}
        used = {c.pdf_style.font_id for c in native}
        if (len(fa) != len(lp.pdf_font) or len(fb) != len(rp.pdf_font)
                or any(k not in fa or k not in fb or fa[k] != fb[k] or not isinstance(fa[k].xref_id,int)
                       or fa[k].xref_id <= 0 for k in used)):
            continue
        left.pdf_paragraph_composition = left.pdf_paragraph_composition[:cut]
        right.pdf_paragraph_composition = moved+right.pdf_paragraph_composition
        left.unicode = left.unicode[:ending.start('tail')].rstrip()
        right.unicode = ending['tail']+' '+right.unicode
        _rejoined_source_paragraphs[id(left)] = left; _rejoined_source_paragraphs[id(right)] = right
        count += 1
    if count:
        with _lock: _counters['rejoinedSourceResearchPhrases'] += count
    return count


def merge_source_bullet_prepositions(styler, page):
    """Join native ``bullet + In`` with its adjacent same-line prose only."""
    config = getattr(styler, 'translation_config', None)
    if config is None or getattr(config, 'skip_translation', False):
        return 0
    count = 0; result = []; index = 0
    while index < len(page.pdf_paragraph):
        left = page.pdf_paragraph[index]; index += 1
        result.append(left)
        if index >= len(page.pdf_paragraph) or not re.fullmatch(r'[•▪◦●]\s+In', left.unicode or ''):
            continue
        right = page.pdf_paragraph[index]
        a = left.pdf_paragraph_composition; b = right.pdf_paragraph_composition
        if (len(a) != 2 or not a[0].pdf_formula or not a[1].pdf_same_style_characters
                or not b or not b[0].pdf_same_style_characters or not re.match(r'[a-z]{2,}\b', right.unicode or '')
                or left.layout_label not in {'plain text','plain_text','text'} or right.layout_label != left.layout_label
                or left.vertical or right.vertical or left.xobj_id != right.xobj_id
                or not _definition_same_style(left.pdf_style,right.pdf_style)):
            continue
        marker = a[0].pdf_formula
        if marker.pdf_curve or marker.pdf_form or ''.join(c.char_unicode for c in marker.pdf_character).strip() != left.unicode[0]:
            continue
        prefix = a[1].pdf_same_style_characters.pdf_character
        head = b[0].pdf_same_style_characters.pdf_character
        if ''.join(c.char_unicode for c in prefix) != 'In' or not head:
            continue
        size = left.pdf_style.font_size
        visible = [c for c in head if (c.char_unicode or '').strip()]
        first = visible[0] if visible else None
        if (first is None or not left.box or not right.box or any(not _finite_character(c)
                or c.formula_layout_id is not None or c.xobj_id != left.xobj_id
                or not _definition_same_style(c.pdf_style,left.pdf_style) for c in prefix+[first])
                or abs(prefix[0].box.y-prefix[-1].box.y) > .08*size
                or abs(first.box.y-prefix[-1].box.y) > .08*size
                or not .05*size <= first.box.x-prefix[-1].box.x2 <= .6*size
                or not -.1*size <= prefix[1].box.x-prefix[0].box.x2 <= .1*size
                or right.box.x < left.box.x2 or abs(right.box.y2-left.box.y2) > .3*size):
            continue
        all_native = _definition_chars(left)+_definition_chars(right)
        if (_definition_compact(''.join(c.char_unicode or '' for c in _definition_chars(left))) != _definition_compact(left.unicode)
                or _definition_compact(''.join(c.char_unicode or '' for c in _definition_chars(right))) != _definition_compact(right.unicode)):
            continue
        if len(set(map(id, all_native))) != len(all_native):
            continue
        left.pdf_paragraph_composition = a+b
        left.unicode = left.unicode+' '+right.unicode
        left.box = copy.copy(left.box)
        left.box.x2 = max(left.box.x2,right.box.x2); left.box.y = min(left.box.y,right.box.y); left.box.y2 = max(left.box.y2,right.box.y2)
        left.first_line_indent = False
        _hanging_indents[id(left)] = (left, prefix[0].box.x-left.box.x)
        _rejoined_source_paragraphs[id(left)] = left
        count += 1; index += 1
    page.pdf_paragraph = result
    if count:
        with _lock: _counters['mergedSourceBulletPrepositions'] += count
    return count


def _page_definition_pair(left_page, right_page):
    if right_page.page_number != left_page.page_number + 1:
        return None
    lb = getattr(getattr(left_page, 'mediabox', None), 'box', None)
    rb = getattr(getattr(right_page, 'mediabox', None), 'box', None)
    if (not lb or not rb or lb != rb
            or any(not isinstance(v, (int,float)) or not math.isfinite(v)
                   for v in (lb.x,lb.y,lb.x2,lb.y2))):
        return None
    left_bodies = [(p,_source_body_box(p)) for p in left_page.pdf_paragraph]
    right_bodies = [(p,_source_body_box(p)) for p in right_page.pdf_paragraph]
    left_bodies = [(p,b) for p,b in left_bodies if b]
    right_bodies = [(p,b) for p,b in right_bodies if b]
    if not left_bodies or not right_bodies:
        return None
    left,a = left_bodies[-1];right,b = right_bodies[0]
    size=max(a[4],b[4]);width=lb.x2-lb.x
    if (left.xobj_id != 0 or right.xobj_id != 0 or right.first_line_indent is not False
            or not left.debug_id or not right.debug_id
            or abs(a[4]-b[4])>.02 or not _definition_same_style(left.pdf_style,right.pdf_style)
            or not .85 <= (a[2]-a[0])/(b[2]-b[0]) <= 1.18
            or a[0] < lb.x+.45*width or b[0] > lb.x+.2*width
            or a[1] > lb.y+.15*(lb.y2-lb.y)
            or any(g[1]<a[1]-.2*size for p,g in left_bodies if p is not left)
            or any(g[3]>b[3]+.2*size for p,g in right_bodies if p is not right)):
        return None
    # Genuine two-column geometry, not unrelated boxed text at two page edges.
    if (not any(g[2]<a[0]-.5*size and abs((g[2]-g[0])-(a[2]-a[0]))<2*size for _,g in left_bodies)
            or not any(g[0]>b[2]+.5*size and abs((g[2]-g[0])-(b[2]-b[0]))<2*size for _,g in right_bodies)):
        return None
    return left,right


def rejoin_source_page_definition_heads(translator, docs):
    """Complete a counted-ratio definition with a source-proven short noun.

    Exact scope: last body in the right column of one page; first body in the
    left column of the immediately next page; a single unclosed plain-text
    '(the ratio of the number of ... to the square/cube of the number of'
    followed by one ordinary lowercase plural noun, '),', and an example.
    This never merges paragraphs, moves formulas or changes fonts/coordinates.
    """
    from babeldoc.format.pdf.document_il import PdfParagraphComposition
    config=getattr(translator,'translation_config',None)
    if config is None or getattr(config,'skip_translation',False):return 0
    minimum=getattr(config,'min_text_length',0)
    repaired=0
    for left_page,right_page in zip(docs.page,docs.page[1:]):
        pair=_page_definition_pair(left_page,right_page)
        if pair is None:continue
        left,right=pair
        ending=re.search(r'\(the ratio of the number of [a-z]{3,24} to the (?:square|cube) of the number of\s*$',left.unicode)
        head=re.match(r'([a-z]{2,23}s\),)(?=\s+e\.g\.:?\s)',right.unicode)
        if not ending or not head:continue
        prefix=left.unicode[:ending.start()]
        if prefix.count('(')!=prefix.count(')'):continue
        fragment=head[1];remainder=right.unicode[len(fragment):].lstrip()
        if len(remainder)<minimum or len(left.unicode)<minimum:continue
        # The definition suffix is one pure prose composition. Formula
        # characters elsewhere in the paragraph play no role in this proof.
        if not left.pdf_paragraph_composition or not right.pdf_paragraph_composition:continue
        tail=left.pdf_paragraph_composition[-1].pdf_same_style_characters
        first=right.pdf_paragraph_composition[0].pdf_same_style_characters
        if not tail or not first or not tail.pdf_character or not first.pdf_character:continue
        if (not _definition_same_style(tail.pdf_style,left.pdf_style) or not _definition_same_style(first.pdf_style,right.pdf_style)
                or _definition_compact(''.join(c.char_unicode or '' for c in _definition_chars(left)))!=_definition_compact(left.unicode)
                or _definition_compact(''.join(c.char_unicode or '' for c in _definition_chars(right)))!=_definition_compact(right.unicode)):
            continue
        raw=''.join(c.char_unicode or '' for c in tail.pdf_character)
        start=raw.rfind('(')
        suffix=raw[start:]
        if (start<0 or _definition_compact(suffix)!=_definition_compact(ending[0])
                or not re.fullmatch(r'[A-Za-z( ]+',suffix)):
            continue
        # Every non-space glyph in the complete source definition is ordinary
        # body text; a mislabeled run cannot smuggle a formula into this join.
        offset=0;definition_chars=[]
        for c in tail.pdf_character:
            if offset>=start:definition_chars.append(c)
            offset+=len(c.char_unicode or '')
        if (not definition_chars or definition_chars[0].char_unicode!='('
                or any(not _finite_character(c) or c.formula_layout_id is not None
                       or c.pdf_character_id is None or c.xobj_id!=0
                       or not _definition_same_style(c.pdf_style,left.pdf_style)
                       for c in definition_chars if (c.char_unicode or '').strip())):
            continue
        # Exact native glyph boundaries, no partial ligature or generated space.
        moved=[];assembled=''
        for char in first.pdf_character:
            if len(assembled)>=len(fragment):break
            moved.append(char);assembled+=char.char_unicode or ''
        if assembled!=fragment or len(moved)!=len(fragment):continue
        kept=first.pdf_character[len(moved):]
        following=next((c for c in kept if (c.char_unicode or '').strip()),None)
        previous=next((c for c in reversed(tail.pdf_character) if (c.char_unicode or '').strip()),None)
        if following is None or previous is None:continue
        size=left.pdf_style.font_size;baseline=moved[0].box.y if moved[0].box else None
        if (baseline is None or any(not _finite_character(c) or c.formula_layout_id is not None
                or c.pdf_character_id is None or c.xobj_id!=0 or not _definition_same_style(c.pdf_style,right.pdf_style)
                or not re.fullmatch(r'[a-z),]',c.char_unicode or '')
                or abs(c.box.y-baseline)>.1*size for c in moved)
                or not _finite_character(previous) or not _finite_character(following)
                or previous.formula_layout_id is not None or following.formula_layout_id is not None
                or abs(moved[0].box.x-right.box.x)>.2*size
                or abs(moved[0].box.y2-right.box.y2)>.2*size
                or any(not -.1*size<=b.box.x-a.box.x2<=.2*size for a,b in zip(moved,moved[1:]))
                or abs(following.box.y-baseline)>.1*size
                or not -.1*size<=following.box.x-moved[-1].box.x2<=1.2*size
                or abs(previous.box.x2-left.box.x2)>size):
            continue
        # Same ID is not sufficient across pages. Require the exact PDF font
        # resource, encoding and complete recorded font metrics to agree.
        source_fonts={f.font_id:f for f in right_page.pdf_font}
        target_fonts={f.font_id:f for f in left_page.pdf_font}
        font=source_fonts.get(right.pdf_style.font_id)
        if (font is None or len([f for f in right_page.pdf_font if f.font_id==font.font_id])!=1
                or len([f for f in left_page.pdf_font if f.font_id==font.font_id])!=1
                or not isinstance(font.xref_id,int) or font.xref_id<=0
                or target_fonts.get(font.font_id)!=font):
            continue
        moved_run,kept_run=copy.copy(first),copy.copy(first)
        moved_run.pdf_character=moved;kept_run.pdf_character=kept
        for run in (moved_run,kept_run):
            run.box=copy.copy(first.box)
            if run.box:
                run.box.x=min(c.box.x for c in run.pdf_character)
                run.box.y=min(c.box.y for c in run.pdf_character)
                run.box.x2=max(c.box.x2 for c in run.pdf_character)
                run.box.y2=max(c.box.y2 for c in run.pdf_character)
        left.pdf_paragraph_composition.append(PdfParagraphComposition(pdf_same_style_characters=moved_run))
        right.pdf_paragraph_composition[0]=PdfParagraphComposition(pdf_same_style_characters=kept_run)
        left.unicode=left.unicode.rstrip()+' '+fragment
        right.unicode=remainder
        _rejoined_source_paragraphs[id(left)]=left
        _rejoined_source_paragraphs[id(right)]=right
        repaired+=1
    if repaired:
        with _lock:_counters['rejoinedSourcePageDefinitionHeads']+=repaired
    return repaired


def split_source_physical_lines(styler, page):
    """Restore source line wraps before assigning formula line IDs.

    A parser PdfLine can contain several complete physical rows. Its one line
    ID then lets a wrapped formula's bounding box absorb unrelated numbers in
    preceding prose. Split only an ordered, same-size body-text wrap with an
    unmistakable leftward reset and a full baseline descent. Original glyphs,
    scripts and their properties are retained; detected multiline equations,
    overprints, fractions and ambiguous writing directions are not touched.
    """
    from babeldoc.format.pdf.document_il import PdfParagraphComposition

    repaired = 0
    for paragraph in page.pdf_paragraph:
        if (paragraph.vertical or paragraph.layout_label not in {'plain text', 'plain_text', 'text'}
                or len(re.findall(r'[A-Za-z]{3,}', paragraph.unicode or '')) < 3):
            continue
        compositions = []
        for comp in paragraph.pdf_paragraph_composition:
            line = comp.pdf_line
            chars = line.pdf_character if line is not None else []
            if (len(chars) < 6 or any(not _finite_character(c) or c.formula_layout_id is not None
                    or (c.xobj_id != paragraph.xobj_id and not (c.xobj_id is None
                        and c.pdf_character_id is None and c.char_unicode == ' '))
                    or not isinstance(c.char_unicode, str)
                    for c in chars)):
                compositions.append(comp)
                continue
            sizes = sorted(c.pdf_style.font_size for c in chars if c.char_unicode.strip())
            if not sizes:
                compositions.append(comp)
                continue
            size = sizes[len(sizes) // 2]
            regular = [c for c in chars if abs(c.pdf_style.font_size - size) <= .02
                       and c.char_unicode.strip()]
            if not regular or len(regular) < len(chars) * .6:
                compositions.append(comp)
                continue
            baseline = regular[0].box.y
            groups, start, valid = [], 0, True
            for index, char in enumerate(chars):
                delta = baseline - char.box.y
                if delta > size * .85:
                    previous = chars[index - 1] if index else None
                    if (previous is None or abs(char.pdf_style.font_size - size) > .02
                            or not size * .85 < delta < size * 1.8
                            or previous.box.x - char.box.x < size * 2
                            or len(chars[start:index]) < 3):
                        valid = False
                        break
                    groups.append(chars[start:index])
                    start, baseline = index, char.box.y
                elif abs(delta) > size * .65:
                    valid = False
                    break
                # Small superscripts/subscripts stay with their physical row.
                if (index > start and char.box.x < chars[index - 1].box.x - .25
                        or char.pdf_style.font_size > size * 1.05):
                    valid = False
                    break
            groups.append(chars[start:])
            if (not valid or len(groups) == 1 or any(len(row) < 3 for row in groups)
                    or any(sum(abs(c.pdf_style.font_size - size) <= .02 for c in row) < len(row) * .6
                           for row in groups)):
                compositions.append(comp)
                continue
            for row in groups:
                physical = copy.copy(line)
                physical.pdf_character = row
                styler.update_line_data(physical)
                compositions.append(PdfParagraphComposition(pdf_line=physical))
            repaired += len(groups) - 1
        paragraph.pdf_paragraph_composition = compositions
    if repaired:
        with _lock:
            _counters['restoredPhysicalLineBreaks'] += repaired
    return repaired


def repair_script_following_formula_offsets(page):
    """Anchor a source-baseline '= number' fragment to the variable, not its o.

    Upstream sometimes measures an adjacent native number formula from a rich
    small script. Once that script is preserved separately, the number is
    visibly raised. Repair only the exact proven wrong anchor; all original
    glyphs and x offsets stay intact, including a separately translated decimal
    tail. This does not adjust compound expressions or genuine superscripts.
    """
    fonts = {f.font_id: f for f in page.pdf_font}
    xfonts = {o.xobj_id: {**fonts, **{f.font_id: f for f in o.pdf_font}}
              for o in page.pdf_xobject}
    repaired = 0
    for paragraph in page.pdf_paragraph:
        if paragraph.vertical:
            continue
        lookup = xfonts.get(paragraph.xobj_id, fonts)
        def family(c):
            f = lookup.get(c.pdf_style.font_id) if c.pdf_style else None
            name = getattr(f, 'name', None)
            return re.sub(r'^[A-Z]{6}\+', '', name) if isinstance(name, str) and name else None
        entries = [(c, comp.pdf_formula is not None)
                   for comp in paragraph.pdf_paragraph_composition
                   for c in _composition_characters(comp)]
        for comp in paragraph.pdf_paragraph_composition:
            f = comp.pdf_formula
            if (f is None or not f.box or f.pdf_curve or f.pdf_form or not f.pdf_character
                    or f.pdf_character[0].char_unicode != '='
                    or not re.fullmatch(r'=\s*[+−-]?\d+(?:[.,]\d+)?\s*',
                                        ''.join(c.char_unicode or '' for c in f.pdf_character))
                    or any(not _finite_character(c) for c in f.pdf_character)
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                           for v in (f.box.x, f.box.y, f.box.x2, f.box.y2, f.x_offset, f.y_offset))):
                continue
            index = next((i for i, (c, _) in enumerate(entries) if c is f.pdf_character[0]), 0)
            if index < 2:
                continue
            base, base_formula = entries[index - 2]
            script, script_formula = entries[index - 1]
            if (base_formula or script_formula or not _finite_character(base) or not _finite_character(script)
                    or not re.fullmatch(r'[A-Za-z]', base.char_unicode or '')
                    or not re.fullmatch(r'[A-Za-z0-9]', script.char_unicode or '')
                    or index > 2 and re.search(r'[A-Za-z0-9_]$', entries[index - 3][0].char_unicode or '')):
                continue
            size = base.pdf_style.font_size
            name = family(base)
            if (not name or family(script) != name or base.xobj_id != paragraph.xobj_id
                    or script.xobj_id != base.xobj_id
                    or not .35 * size <= script.pdf_style.font_size < .85 * size
                    or not -.65 * size <= script.box.y - base.box.y <= -.12 * size
                    or not -.15 * size <= script.box.x - base.box.x2 <= .6 * size
                    or not -.15 * size <= f.pdf_character[0].box.x - script.box.x2 <= .6 * size
                    or any(c.xobj_id != base.xobj_id or family(c) != name
                           or abs(c.pdf_style.font_size - size) > .02
                           or abs(c.box.y - base.box.y) > .05 for c in f.pdf_character)
                    or abs(f.y_offset - (f.box.y - script.box.y)) > .05):
                continue
            candidates = [c for pos, (c, is_formula) in enumerate(entries)
                          if not is_formula and _finite_character(c)
                          and re.fullmatch(r'[A-Za-z]', c.char_unicode or '')
                          and (pos == 0 or not re.search(r'[A-Za-z0-9_]$', entries[pos - 1][0].char_unicode or ''))
                          and c.xobj_id == base.xobj_id and family(c) == name
                          and abs(c.pdf_style.font_size - size) <= .02
                          and -.15 * size <= script.box.x - c.box.x2 <= .6 * size
                          and -.65 * size <= script.box.y - c.box.y <= -.12 * size]
            if len(candidates) != 1 or candidates[0] is not base:
                continue
            f.y_offset = f.box.y - base.box.y
            repaired += 1
    if repaired:
        with _lock:
            _counters['repairedScriptFollowingFormulaOffsets'] += repaired
    return repaired


def register_script_bindings(page):
    """Record only unique source-proven Latin base / leading subscript pairs."""
    fonts = {f.font_id: f for f in page.pdf_font}
    xfonts = {o.xobj_id: {**fonts, **{f.font_id: f for f in o.pdf_font}}
              for o in page.pdf_xobject}
    found = 0
    for paragraph in page.pdf_paragraph:
        if paragraph.vertical:
            continue
        lookup = xfonts.get(paragraph.xobj_id, fonts)
        def family(c):
            f = lookup.get(c.pdf_style.font_id) if c.pdf_style else None
            name = getattr(f, 'name', None)
            return re.sub(r'^[A-Z]{6}\+', '', name) if isinstance(name, str) and name else None
        entries = [(c, comp.pdf_formula is not None)
                   for comp in paragraph.pdf_paragraph_composition
                   for c in _composition_characters(comp)]
        for comp in paragraph.pdf_paragraph_composition:
            f = comp.pdf_formula
            if (f is None or not f.box or f.pdf_curve or f.pdf_form
                    or f.is_corner_mark is False or not f.pdf_character
                    or any(not _finite_character(c) for c in f.pdf_character)
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                           for v in (f.box.x, f.box.y, f.box.x2, f.box.y2,
                                     f.x_offset, f.y_offset))):
                continue
            script = f.pdf_character[0]
            if not re.fullmatch(r'[A-Za-z0-9]', script.char_unicode or ''):
                continue
            index = next((i for i, (c, _) in enumerate(entries) if c is script), 0)
            if not index:
                continue
            base, is_formula = entries[index - 1]
            if (is_formula or not _finite_character(base)
                    or not re.fullmatch(r'[A-Za-z]', base.char_unicode or '')
                    or index > 1 and re.search(r'[A-Za-z0-9_]$', entries[index - 2][0].char_unicode or '')):
                continue
            size = base.pdf_style.font_size
            name = family(base)
            if (not name or family(script) != name or base.xobj_id != paragraph.xobj_id
                    or any(c.xobj_id != base.xobj_id for c in f.pdf_character)
                    or not .35 * size <= script.pdf_style.font_size < .85 * size
                    or not -.65 * size <= script.box.y - base.box.y <= -.12 * size
                    or not -.15 * size <= script.box.x - base.box.x2 <= .6 * size
                    or abs(f.y_offset - (f.box.y - base.box.y)) > .05
                    or any(c.box.x < script.box.x - .1 or abs(c.box.y - base.box.y) > .7 * size
                           for c in f.pdf_character[1:])):
                continue
            candidates = [c for pos, (c, is_formula) in enumerate(entries)
                          if not is_formula and _finite_character(c)
                          and re.fullmatch(r'[A-Za-z]', c.char_unicode or '')
                          and (pos == 0 or not re.search(r'[A-Za-z0-9_]$', entries[pos - 1][0].char_unicode or ''))
                          and c.xobj_id == base.xobj_id and family(c) == name
                          and abs(c.pdf_style.font_size - size) <= .02
                          and -.15 * size <= script.box.x - c.box.x2 <= .6 * size
                          and -.65 * size <= script.box.y - c.box.y <= -.12 * size]
            if len(candidates) != 1 or candidates[0] is not base:
                continue
            with _lock:
                if id(f) not in _script_bindings:
                    _script_bindings[id(f)] = (f, base.char_unicode, name, size, base.xobj_id)
                    found += 1
    if found:
        with _lock:
            _counters['registeredScriptBindings'] += found
    return found


class _BoundScriptUnit:
    """Temporary line-layout atom; both children still use upstream relocation."""
    def __init__(self, base, script):
        self.base, self.script = base, script

    def __getattr__(self, name):
        return getattr(self.base, name)

    @property
    def width(self):
        return self.base.width + self.script.width + self.script.formular.x_offset

    @property
    def height(self):
        return max(self.base.height, self.script.height)

    @property
    def box(self):
        from babeldoc.format.pdf.document_il import Box
        b = self.base.box
        return Box(b.x, b.y, b.x + self.width, b.y + self.height)

    @property
    def can_break_line(self):
        return True

    def relocate(self, x, y, scale):
        base = self.base.relocate(x, y, scale)
        script = self.script.relocate(base.box.x2, y, scale)
        return _BoundScriptUnit(base, script)


def _bound_typesetting_units(units):
    result = []
    for unit in units:
        f = unit.formular
        with _lock:
            binding = _script_bindings.get(id(f)) if f is not None else None
        if binding and binding[0] is f and result and not isinstance(result[-1], _BoundScriptUnit):
            base = result[-1]
            previous = result[-2].try_get_unicode() if len(result) > 1 else None
            style = base.char.pdf_style if base.char else getattr(base, 'style', None)
            original_font = getattr(base, 'original_font', None)
            name = getattr(original_font, 'name', None)
            family = re.sub(r'^[A-Z]{6}\+', '', name) if isinstance(name, str) else None
            # Only generated base text is joined. Original passthrough glyphs
            # retain their source positions and do not need line-layout repair.
            if (base.unicode == binding[1] and base.formular is None and style
                    and family == binding[2] and base.xobj_id == binding[4]
                    and abs(base.font_size - binding[3]) <= .02
                    and not re.search(r'[A-Za-z0-9_]$', previous or '')
                    and math.isfinite(base.width) and base.width > 0
                    and math.isfinite(unit.width + f.x_offset) and unit.width + f.x_offset > 0):
                result[-1] = _BoundScriptUnit(base, unit)
                continue
        result.append(unit)
    return result


def _guard_layout_typesetting_units(self, typesetting_units, box, scale, line_skip,
                                   paragraph, use_english_line_break=True):
    grouped = _bound_typesetting_units(typesetting_units)
    indent = _hanging_indents.get(id(paragraph), (None, 0))
    indent = indent[1] * scale if indent[0] is paragraph else 0
    if any(unit.formular and (unit.formular.pdf_curve or unit.formular.pdf_form) for unit in typesetting_units):
        indent = 0  # Do not shift embedded vector/form objects without a transform.
    if indent:
        box = copy.copy(box)
        box.x += indent
    if any(isinstance(unit, _BoundScriptUnit) and unit.width * scale > box.x2 - box.x
           for unit in grouped):
        return [], False
    baselines = {}
    tracked = [_BaselineTrackingUnit(unit, baselines) for unit in grouped] if indent else grouped
    laid_out, fits = _original_layout_typesetting_units(
        self, tracked, box, scale, line_skip, paragraph, use_english_line_break)
    if indent and laid_out:
        # Layout inside the continuation column, then move only the first line
        # left by the source-proven hanging indent. The first line is slightly
        # conservative in width, so it cannot overflow the original box.
        first_y = baselines.get(id(laid_out[0]))
        for index, unit in enumerate(laid_out):
            if first_y is None or baselines.get(id(unit)) != first_y:
                break
            _shift_rendered_unit_x(unit, -indent)
    # Upstream consumers only see standard units; no special render or PDF path.
    return [child for unit in laid_out
            for child in ((unit.base, unit.script) if isinstance(unit, _BoundScriptUnit) else (unit,))], fits


class _BaselineTrackingUnit:
    """Observe upstream's layout baseline before a script offset is applied."""
    def __init__(self, unit, baselines):
        self.unit, self.baselines = unit, baselines

    def __getattr__(self, name):
        return getattr(self.unit, name)

    def relocate(self, x, y, scale):
        result = self.unit.relocate(x, y, scale)
        self.baselines[id(result)] = y
        return result


def _shift_rendered_unit_x(unit, delta):
    # Units have already been relocated. Relocating a formula again would apply
    # its script offset twice. Shift output boxes horizontally only instead.
    if isinstance(unit, _BoundScriptUnit):
        _shift_rendered_unit_x(unit.base, delta)
        _shift_rendered_unit_x(unit.script, delta)
        return
    boxes = []
    if unit.char:
        boxes.append(unit.char.box)
        if unit.char.visual_bbox:
            boxes.append(unit.char.visual_bbox.box)
    elif unit.formular:
        boxes.append(unit.formular.box)
        for char in unit.formular.pdf_character:
            boxes.append(char.box)
            if char.visual_bbox:
                boxes.append(char.visual_bbox.box)
    elif unit.unicode:
        unit.x += delta
    seen = set()
    for box in boxes:
        if box is not None and id(box) not in seen:
            box.x += delta
            box.x2 += delta
            seen.add(id(box))
    unit.box_cache = None


def _guard_is_formulas_font(font_name, formular_font_pattern):
    if _original_is_formulas_font(font_name, formular_font_pattern):
        return True
    name = re.sub(r'^[A-Z]{6}\+', '', font_name) if isinstance(font_name, str) else ''
    if name in _LEGACY_MATH_FONTS:
        with _lock:
            _counters['legacyMathFontMatches'] += 1
        return True
    return False


def _guard_is_translatable_formula(self, formula):
    if id(formula) in getattr(_formula_context, 'protected', ()):
        return False
    return _original_is_translatable_formula(self, formula)


def _guard_process_translatable_formulas(self, page):
    bind_source_formula_fragments(self, page)
    fonts = {f.font_id: f for f in page.pdf_font}
    xfonts = {o.xobj_id: {**fonts, **{f.font_id: f for f in o.pdf_font}}
              for o in page.pdf_xobject}
    protected = set()
    for paragraph in page.pdf_paragraph:
        lookup = xfonts.get(paragraph.xobj_id, fonts)
        for comp in paragraph.pdf_paragraph_composition:
            formula = comp.pdf_formula
            if formula is None or not _original_is_translatable_formula(self, formula):
                continue
            if any(c.pdf_style and c.pdf_style.font_id in lookup and
                   re.sub(r'^[A-Z]{6}\+', '', lookup[c.pdf_style.font_id].name)
                   in _LEGACY_SYMBOL_FONTS for c in formula.pdf_character):
                protected.add(id(formula))
    previous = getattr(_formula_context, 'protected', None)
    _formula_context.protected = protected
    try:
        result = _original_process_translatable_formulas(self, page)
        # An ordinary decimal suffix may initially be PdfLine('.') followed
        # by a numeric PdfFormula. Only after numeric demotion is its complete
        # source text available to the narrow native-decimal binding below.
        bind_source_formula_fragments(self, page)
        return result
    finally:
        if previous is None:
            del _formula_context.protected
        else:
            _formula_context.protected = previous
        if protected:
            with _lock:
                _counters['preservedLegacySymbolFormulas'] += len(protected)


def _source_glyph_run(chars, xobj_id):
    """A compact single-baseline run, allowing only parser-created spaces."""
    visible = [c for c in chars if c.char_unicode and c.char_unicode.strip()]
    if not visible or any(not _finite_character(c) for c in chars):
        return False
    anchor = visible[0]
    size = anchor.pdf_style.font_size
    return (all((c.xobj_id == xobj_id or c.xobj_id is None and c.pdf_character_id is None
                 and c.char_unicode == ' ')
                and abs(c.box.y - anchor.box.y) <= .1 * size
                and abs(c.pdf_style.font_size - size) <= .02 for c in chars)
            and all(-.25 <= b.box.x - a.box.x2 <= .35 * size for a, b in zip(chars, chars[1:])))


def bind_source_formula_fragments(styler, page):
    """Keep native '_descriptor = decimal' fragments in their source formula.

    No words are inferred from output: an explicit underscore, a short attached
    letter run and an adjacent same-line formula containing '=' prove the
    descriptor. A decimal continuation requires a native leading digit and
    touching original dot/digit glyphs. Curves, forms, scripts and real word
    spaces reject the repair. All original glyphs and their properties survive.
    """
    from babeldoc.format.pdf.document_il import PdfParagraphComposition
    descriptors = decimals = 0
    for paragraph in page.pdf_paragraph:
        if paragraph.vertical:
            continue
        comps = paragraph.pdf_paragraph_composition
        index = 0
        while index < len(comps):
            left = comps[index].pdf_formula
            if left is None or left.pdf_curve or left.pdf_form:
                index += 1
                continue
            # A descriptor is a complete text island inside a source formula,
            # not a word following a formula or a neighboring prose connector.
            if index + 2 < len(comps):
                middle, right = comps[index + 1].pdf_line, comps[index + 2].pdf_formula
                letters = middle.pdf_character if middle is not None else []
                text = ''.join(c.char_unicode or '' for c in letters)
                if (right is not None and not right.pdf_curve and not right.pdf_form
                        and left.line_id == right.line_id and left.pdf_character
                        and left.pdf_character[-1].char_unicode == '_'
                        and re.fullmatch(r'[A-Za-z]{1,8}', text)
                        and '=' in ''.join(c.char_unicode or '' for c in right.pdf_character)
                        and all(c.pdf_style.font_id == left.pdf_character[-1].pdf_style.font_id
                                for c in letters)
                        and _source_glyph_run(left.pdf_character + letters + right.pdf_character,
                                              paragraph.xobj_id)):
                    merged = copy.copy(left)
                    merged.pdf_character = left.pdf_character + letters + right.pdf_character
                    styler.update_formula_data(merged)
                    comps[index:index + 3] = [PdfParagraphComposition(pdf_formula=merged)]
                    left = merged
                    descriptors += 1
            if index + 1 < len(comps) and comps[index + 1].pdf_line is not None:
                following_lines = []
                for following_comp in comps[index + 1:]:
                    if following_comp.pdf_line is None:
                        break
                    following_lines.append(following_comp.pdf_line)
                chars = [c for line in following_lines for c in line.pdf_character]
                text = ''.join(c.char_unicode or '' for c in chars)
                match = re.match(r'\.\d+(?![A-Za-z0-9_])', text)
                original = ''.join(c.char_unicode or '' for c in left.pdf_character)
                if (match and re.search(r'=\s*[+−-]?\d+$', original)
                        and all(len(c.char_unicode or '') == 1 for c in chars[:match.end()])
                        and len(chars) >= match.end()
                        and _source_glyph_run(left.pdf_character + chars[:match.end()], paragraph.xobj_id)
                        and all(c.pdf_style.font_id == left.pdf_character[-1].pdf_style.font_id
                                for c in chars[:match.end()])):
                    left.pdf_character = left.pdf_character + chars[:match.end()]
                    styler.update_formula_data(left)
                    remaining = match.end()
                    while remaining:
                        following = comps[index + 1].pdf_line
                        consumed = min(remaining, len(following.pdf_character))
                        rest = following.pdf_character[consumed:]
                        if rest:
                            remainder = copy.copy(following)
                            remainder.pdf_character = rest
                            styler.update_line_data(remainder)
                            comps[index + 1] = PdfParagraphComposition(pdf_line=remainder)
                        else:
                            del comps[index + 1]
                        remaining -= consumed
                    decimals += 1
            index += 1
    if descriptors or decimals:
        with _lock:
            _counters['boundSourceFormulaDescriptors'] += descriptors
            _counters['boundSourceFormulaDecimals'] += decimals
    return descriptors + decimals


def bind_source_subscript_indices(styler, page):
    """Rejoin a native two-index subscript split around an ordinary comma.

    Only base/index, comma and second index on one source line qualify. The
    small glyphs must be contiguous, equally sized and lowered together. All
    original glyph objects, graphics and positions remain unchanged.
    """
    from babeldoc.format.pdf.document_il import PdfParagraphComposition
    repaired = 0
    for paragraph in page.pdf_paragraph:
        if paragraph.vertical:
            continue
        comps = paragraph.pdf_paragraph_composition
        index = 0
        while index + 2 < len(comps):
            left, middle, right = (comps[index].pdf_formula,
                                  comps[index+1].pdf_same_style_characters,
                                  comps[index+2].pdf_formula)
            index += 1
            if (left is None or middle is None or right is None
                    or left.pdf_curve or left.pdf_form or right.pdf_curve or right.pdf_form
                    or left.line_id is None or left.line_id != right.line_id
                    or left.y_offset or right.y_offset
                    or len(left.pdf_character) != 2 or len(middle.pdf_character) != 1):
                continue
            base, first = left.pdf_character
            comma = middle.pdf_character[0]
            visible = [c for c in right.pdf_character if (c.char_unicode or '').strip()]
            if len(visible) != 1 or right.pdf_character[0] is not visible[0]:
                continue
            second = visible[0]
            native = [base, first, comma, second]
            if (any(not _finite_character(c) or c.vertical
                    or c.xobj_id != paragraph.xobj_id or c.formula_layout_id is not None
                    or c.pdf_character_id is None or c.render_order is None for c in native)
                    or not all(re.fullmatch('[A-Za-z]', c.char_unicode or '') for c in (base,first,second))
                    or comma.char_unicode != ','):
                continue
            big, small = base.pdf_style.font_size, first.pdf_style.font_size
            if (not .6 <= small/big <= .8
                    or not -.4*big <= first.box.y-base.box.y <= -.12*big
                    or any(abs(c.pdf_style.font_size-small) > .02 or abs(c.box.y-first.box.y) > .02
                           for c in (comma,second))
                    or not base.pdf_style.font_id == first.pdf_style.font_id == second.pdf_style.font_id
                    or any(c.pdf_style.graphic_state != base.pdf_style.graphic_state for c in native)
                    or not -.15*big <= first.box.x-base.box.x2 <= .2*big
                    or any(not -.02 <= b.box.x-a.box.x2 <= .15*small for a,b in zip(native[1:],native[2:]))
                    or any(b.render_order != a.render_order+1 for a,b in zip(native,native[1:]))
                    or any(c.char_unicode != ' ' or c.pdf_character_id is not None or c.xobj_id is not None
                           for c in right.pdf_character[1:])):
                continue
            merged = copy.copy(left)
            merged.pdf_character = left.pdf_character + middle.pdf_character + right.pdf_character
            styler.update_formula_data(merged)
            comps[index-1:index+2] = [PdfParagraphComposition(pdf_formula=merged)]
            repaired += 1
    if repaired:
        with _lock:
            _counters['boundSourceSubscriptIndices'] += repaired
    return repaired


def _guard_should_split_formula(self, formula):
    if id(formula) in getattr(_formula_context, 'encoded_commas', ()):
        return False
    return _original_should_split_formula(self, formula)


def _guard_process_comma_formulas(self, page):
    fonts = {f.font_id: f for f in page.pdf_font}
    xfonts = {o.xobj_id: {**fonts, **{f.font_id: f for f in o.pdf_font}}
              for o in page.pdf_xobject}
    protected = set()
    for paragraph in page.pdf_paragraph:
        lookup = xfonts.get(paragraph.xobj_id, fonts)
        for comp in paragraph.pdf_paragraph_composition:
            formula = comp.pdf_formula
            if formula is None or not _original_should_split_formula(self, formula):
                continue
            if any(c.char_unicode == ',' and c.pdf_style and c.pdf_style.font_id in lookup and
                   re.sub(r'^[A-Z]{6}\+', '', lookup[c.pdf_style.font_id].name)
                   in _LEGACY_SYMBOL_FONTS for c in formula.pdf_character):
                protected.add(id(formula))
    previous = getattr(_formula_context, 'encoded_commas', None)
    _formula_context.encoded_commas = protected
    try:
        return _original_process_comma_formulas(self, page)
    finally:
        if previous is None:
            del _formula_context.encoded_commas
        else:
            _formula_context.encoded_commas = previous
        if protected:
            with _lock:
                _counters['preservedLegacyCommaFormulas'] += len(protected)


def _is_webdings(font):
    name = getattr(font, "name", None)
    if not isinstance(name, str):
        return False
    # PDF subset prefixes are six ASCII uppercase letters followed by '+'.
    return re.sub(r"^[A-Z]{6}\+", "", name).casefold() == "webdings"


def normalize_page_markers(page):
    """Change interpretation only, never substitute or remove the PDF glyph."""
    page_fonts = {font.font_id: font for font in page.pdf_font}
    xobject_fonts = {
        xobject.xobj_id: {**page_fonts, **{font.font_id: font for font in xobject.pdf_font}}
        for xobject in page.pdf_xobject
    }
    normalized = 0
    for character in page.pdf_character:
        if character.pdf_character_id != 31 or character.char_unicode != "\x1f":
            continue
        style = character.pdf_style
        if style is None:
            continue
        fonts = xobject_fonts.get(character.xobj_id, page_fonts)
        if not _is_webdings(fonts.get(style.font_id)):
            continue
        # U+2022 triggers the upstream formula-preservation path. The visible
        # result remains the original square because pdf_character_id stays 31.
        character.char_unicode = "•"
        normalized += 1
    if normalized:
        with _lock:
            _counters["normalizedWebdingsBullets"] += normalized
            _counters["pagesWithNormalizedBullets"] += 1
    return normalized


def _guard_process_page(self, page):
    normalize_page_markers(page)
    result = _original_process_page(self, page)
    if not preserve_alphabetical_index(page):
        preserve_copyright_blocks(page)
        merge_caption_closers(self, page)
        merge_caption_fragments(self, page)
        merge_source_terminal_punctuation(self, page)
        merge_source_year_range_fragments(self, page)
        preserve_trailing_equations(self, page)
        preserve_toc_leaders(self, page)
        split_structured_entries(self, page)
    # Links are mapped only after source paragraph boundaries are final.
    import pdf_links
    pdf_links.capture_source(page)
    return result


_ENTRY_NUMBER = re.compile(r'^\s*(\d{1,3})(?:([.)\]])\s*|\s+)(?=[A-Za-z])')
_ENTRY_LETTER = re.compile(r'^\s*\(([a-z])\)\s*(?=[A-Za-z])')
_ENTRY_AUTHOR = re.compile(r'^[^\W\d_][\w’\x27.\-]+(?:[ -][^\W\d_][\w’\x27.\-]+){0,2},\s+[A-Z](?:[.-][A-Z])*\.')


def _entry_marker(row, letter=False):
    """Read a marker only at a real glyph boundary with visible source spacing."""
    text = ''.join(c.char_unicode for c in row)
    match = (_ENTRY_LETTER if letter else _ENTRY_NUMBER).match(text)
    if not match:
        return None
    offset, prefix = 0, []
    for body in row:
        if offset == match.end():
            break
        offset += len(body.char_unicode)
        prefix.append(body)
    else:
        return None
    if offset != match.end() or not prefix:
        return None
    visible = [c for c in prefix if c.char_unicode.strip()]
    if not visible:
        return None
    first, last = visible[0], visible[-1]
    size = first.pdf_style.font_size
    # PDFs often contain a drawn gap after `1.` without a space character.
    # A dot glued to the next word is not enough to infer a list boundary.
    if body.box.x - last.box.x2 < size * .1:
        return None
    return dict(value=ord(match[1]) if letter else int(match[1]),
                left=first.box.x, right=last.box.x2, body=body.box.x,
                explicit=letter or bool(match[2]), size=size)


def _aligned_entry_markers(markers):
    """Accept left-aligned labels or right-aligned bibliography numbers."""
    return bool(markers and (
        max(m['left'] for m in markers) - min(m['left'] for m in markers) < 1 or
        max(m['right'] for m in markers) - min(m['right'] for m in markers) < 1))


def _source_reference_date_row(rows, index, body_x):
    """A complete access date continuing the preceding native source row."""
    if index == 0 or not re.search(r'\bAccessed\s*$', ''.join(c.char_unicode for c in rows[index - 1])):
        return False
    text = ''.join(c.char_unicode for c in rows[index]).strip()
    months = ('January', 'February', 'March', 'April', 'May', 'June',
              'July', 'August', 'September', 'October', 'November', 'December')
    match = re.fullmatch(r'([1-3]?\d)\s+(' + '|'.join(months) + r')\s+((?:19|20)\d{2})\.?', text)
    first = next((c for c in rows[index] if c.char_unicode.strip()), None)
    if not match or first is None or abs(first.box.x - body_x) >= 1:
        return False
    from datetime import date
    try:
        date(int(match[3]), months.index(match[2]) + 1, int(match[1]))
    except ValueError:
        return False
    return True


def _restore_detached_list_markers(finder, page):
    """Attach a paired `(a)`, `(b)` source run to its unique body rows.

    Some layout boxes exclude labels and leave them in the original-glyph
    layer. Require two or more sequential, same-column labels, one paragraph,
    matching original font/size/baselines and a small clear horizontal gap.
    Move the original glyph objects; never synthesize or rewrite a label.
    """
    from babeldoc.format.pdf.document_il import PdfLine, PdfParagraphComposition
    rows_by_paragraph = [(p, _source_rows(p)) for p in page.pdf_paragraph
                         if p.layout_label in {'plain text', 'text', 'reference'}]
    candidates = []
    raw = page.pdf_character
    for index in range(len(raw) - 2):
        chars = raw[index:index + 3]
        if not (chars[0].char_unicode == '(' and chars[2].char_unicode == ')' and
                isinstance(chars[1].char_unicode, str) and re.fullmatch(r'[a-z]', chars[1].char_unicode)):
            continue
        if any(not _finite_character(c) for c in chars):
            continue
        first = chars[0]
        size = first.pdf_style.font_size
        if any(c.pdf_style.font_id != first.pdf_style.font_id or
               abs(c.pdf_style.font_size - size) > .02 or c.xobj_id != first.xobj_id or
               abs(c.box.y - first.box.y) > size * .1 for c in chars):
            continue
        if any(not -.1 <= b.box.x - a.box.x2 <= size * .25 for a, b in zip(chars, chars[1:])):
            continue
        matches = []
        for paragraph, rows in rows_by_paragraph:
            if not rows or paragraph.xobj_id != first.xobj_id:
                continue
            for row_index, row in enumerate(rows):
                body = next(c for c in row if c.char_unicode.strip())
                if (not re.match(r'^[A-Za-z]', body.char_unicode) or
                        body.pdf_style.font_id != first.pdf_style.font_id or
                        abs(body.pdf_style.font_size - size) > .02 or
                        abs(body.box.y - first.box.y) > size * .15 or
                        not size * .5 <= body.box.x - chars[-1].box.x2 <= size * 3):
                    continue
                matches.append((paragraph, rows, row_index, body.box.x))
        if len(matches) == 1:
            paragraph, rows, row_index, body_x = matches[0]
            candidates.append(dict(paragraph=paragraph, rows=rows, index=row_index,
                                   chars=chars, value=ord(chars[1].char_unicode),
                                   left=first.box.x, body=body_x, size=size))
    moved = set()
    for paragraph, rows in rows_by_paragraph:
        group = sorted([c for c in candidates if c['paragraph'] is paragraph], key=lambda c: c['index'])
        if (len(group) < 2 or group[0]['index'] != 0 or
                len({c['index'] for c in group}) != len(group) or
                any(b['value'] != a['value'] + 1 for a, b in zip(group, group[1:])) or
                max(c['left'] for c in group) - min(c['left'] for c in group) >= 1 or
                max(c['body'] for c in group) - min(c['body'] for c in group) >= 1 or
                max(c['size'] for c in group) - min(c['size'] for c in group) > .02):
            continue
        ids = [id(char) for entry in group for char in entry['chars']]
        if len(set(ids)) != len(ids) or moved.intersection(ids):
            continue
        for entry in group:
            rows[entry['index']] = entry['chars'] + rows[entry['index']]
        paragraph.pdf_paragraph_composition = [PdfParagraphComposition(pdf_line=PdfLine(pdf_character=row))
                                               for row in rows]
        for comp in paragraph.pdf_paragraph_composition:
            finder.update_line_data(comp.pdf_line)
        finder.update_paragraph_data(paragraph, update_unicode=True)
        moved.update(ids)
    if moved:
        page.pdf_character = [c for c in raw if id(c) not in moved]
        with _lock:
            _counters['restoredDetachedListMarkers'] += len(moved) // 3
    return len(moved) // 3


def _source_rows(paragraph, legacy_bullets=()):
    """Recover physical rows without touching source glyphs or script offsets."""
    if paragraph.vertical or any(c.pdf_line is None for c in paragraph.pdf_paragraph_composition):
        return None
    chars = [c for comp in paragraph.pdf_paragraph_composition for c in comp.pdf_line.pdf_character]
    if not chars or any(not _finite_character(c) or not isinstance(c.char_unicode, str) for c in chars):
        return None
    rows, row, anchor = [], [], None
    for char in chars:
        if not char.char_unicode.strip():
            row.append(char)
            continue
        if anchor is not None and (char.box.y < anchor.box.y - anchor.pdf_style.font_size * .65
                and char.box.x < row[-1].box.x - anchor.pdf_style.font_size):
            rows.append(row)
            row, anchor = [], None
        # A proven legacy bullet is larger and lower than its body baseline.
        # Use the following body glyph as the row anchor, without moving or
        # changing either glyph. Other large/script characters keep old rules.
        if anchor is None and id(char) not in legacy_bullets:
            anchor = char
        row.append(char)
    if row:
        rows.append(row)
    # Actual lines must run top to bottom. A stream crossing into another
    # column or with ambiguous vertical ordering is not resegmented.
    visible = [[c for c in row if c.char_unicode.strip()] for row in rows]
    if any(not row for row in visible) or any(
            b[0].box.y >= a[0].box.y for a, b in zip(visible, visible[1:])):
        return None
    return rows


def _source_legacy_bullet_markers(page):
    """Prove repeated native AdvP7CA8/CID78 bullets by list geometry.

    The exact legacy symbol is already protected as a formula downstream.
    Its misleading Unicode N stays unchanged. Require separate physical rows,
    same-column repeated markers, a smaller body in a different font, and
    aligned hanging continuation rows; a font/name match alone proves nothing.
    """
    fonts = {font.font_id: font for font in page.pdf_font}
    xfonts = {o.xobj_id: {**fonts, **{f.font_id: f for f in o.pdf_font}}
              for o in page.pdf_xobject}
    candidates = []
    for paragraph in page.pdf_paragraph:
        if (paragraph.vertical or paragraph.layout_label not in {'plain text', 'text', 'reference'}
                or any(c.pdf_line is None for c in paragraph.pdf_paragraph_composition)):
            continue
        chars = [c for comp in paragraph.pdf_paragraph_composition for c in comp.pdf_line.pdf_character
                 if isinstance(c.char_unicode, str) and c.char_unicode.strip()]
        if any(not _finite_character(c) for c in chars):
            continue
        lookup = xfonts.get(paragraph.xobj_id, fonts)
        for index, marker in enumerate(chars[:-1]):
            family = getattr(lookup.get(marker.pdf_style.font_id), 'name', '') or ''
            if (marker.char_unicode != 'N' or marker.pdf_character_id != 78
                    or re.sub(r'^[A-Z]{6}\+', '', family) != 'AdvP7CA8'):
                continue
            body = chars[index + 1]
            size = body.pdf_style.font_size
            previous = chars[index - 1] if index else None
            if (not re.match(r'^[A-Z]', body.char_unicode)
                    or marker.xobj_id != body.xobj_id
                    or marker.pdf_style.font_id == body.pdf_style.font_id
                    or not 1.5 <= marker.pdf_style.font_size / size <= 2
                    or not size * .3 <= body.box.x - marker.box.x2 <= size * 2
                    or not marker.box.y - size * .1 <= body.box.y <= marker.box.y + size * .3
                    or body.box.y2 > marker.box.y2 + size * .1
                    or (previous is not None and not
                        (previous.box.y > marker.box.y + size * .65
                         and previous.box.x > marker.box.x + size))):
                continue
            candidates.append(dict(paragraph=paragraph, marker=marker, body=body.box.x, size=size))
    provisional = {id(c['marker']): c for c in candidates}
    verified = []
    for candidate in candidates:
        rows = _source_rows(candidate['paragraph'], provisional)
        if not rows:
            continue
        starts = [next(c for c in row if c.char_unicode.strip()) for row in rows]
        start = next((i for i, c in enumerate(starts) if c is candidate['marker']), None)
        if start is None:
            continue
        end = next((i for i in range(start + 1, len(rows)) if id(starts[i]) in provisional), len(rows))
        if end - start < 2 or any(abs(c.box.x - candidate['body']) >= 1 for c in starts[start + 1:end]):
            continue
        verified.append(candidate)
    result = {}
    for candidate in verified:
        marker = candidate['marker']
        peers = [c for c in verified if c['marker'].xobj_id == marker.xobj_id
                 and c['marker'].pdf_style.font_id == marker.pdf_style.font_id
                 and abs(c['marker'].pdf_style.font_size - marker.pdf_style.font_size) <= .02
                 and abs(c['size'] - candidate['size']) <= .02
                 and abs(c['marker'].box.x - marker.box.x) < 1
                 and abs(c['body'] - candidate['body']) < 1]
        if len(peers) >= 2:
            result[id(marker)] = candidate
    return result


def split_structured_entries(finder, page):
    """Restore explicit list items and hanging bibliography entry boundaries.

    Numbered lists require consecutive source labels aligned at their left or
    right edge. Bibliographies require repeated author/initial headers and
    indented continuation rows. No page numbers or paper titles are used.
    """
    from babeldoc.format.pdf.document_il import PdfLine, PdfParagraphComposition
    _restore_detached_list_markers(finder, page)
    legacy_bullets = _source_legacy_bullet_markers(page)
    result, splits, hanging = [], 0, 0
    for paragraph in page.pdf_paragraph:
        rows = _source_rows(paragraph, legacy_bullets)
        if (not rows or paragraph.layout_label not in {'plain text', 'text', 'reference'}):
            result.append(paragraph)
            continue
        texts = [''.join(c.char_unicode for c in row).strip() for row in rows]
        starts = [next(c for c in row if c.char_unicode.strip()) for row in rows]
        left = min(c.box.x for c in starts)
        size = max(c.pdf_style.font_size for c in starts)
        numeric = [(i, m) for i, row in enumerate(rows) if (m := _entry_marker(row))]
        # A continuation can start with a date such as '12 August 2014'.
        # Exclude it only with the complete previous-row 'Accessed' grammar,
        # a valid date, and the established reference body column. Mixed
        # punctuated/unpunctuated lists must retain their existing boundaries.
        explicit = [(i, m) for i, m in numeric if m['explicit']]
        if (len(explicit) >= 2 and _aligned_entry_markers([m for _, m in explicit])
                and all(b[1]['value'] == a[1]['value'] + 1 for a, b in zip(explicit, explicit[1:]))
                and all(m['explicit'] or _source_reference_date_row(rows, i, explicit[-1][1]['body'])
                        for i, m in numeric)):
            numeric = explicit
        letters = [(i, m) for i, row in enumerate(rows) if (m := _entry_marker(row, letter=True))]
        authors = [i for i, text in enumerate(texts)
                   if _ENTRY_AUTHOR.match(text) and abs(starts[i].box.x - left) < 1]
        continuation = [c.box.x - left for c in starts if size * .5 <= c.box.x - left <= size * 3]
        is_numbered = (len(numeric) >= 2 and _aligned_entry_markers([m for _, m in numeric]) and
                       all(b[1]['value'] == a[1]['value'] + 1 for a, b in zip(numeric, numeric[1:])))
        is_lettered = (len(letters) >= 2 and _aligned_entry_markers([m for _, m in letters]) and
                       all(b[1]['value'] == a[1]['value'] + 1 for a, b in zip(letters, letters[1:])))
        is_references = len(authors) >= 2 and continuation and bool(re.search(r'\b(?:19|20)\d{2}\b', ' '.join(texts)))
        bullet_rows = [i for i,text in enumerate(texts) if re.match(r'^[•▪◦●]\s*\S', text)
                       or id(starts[i]) in legacy_bullets]
        is_bulleted = (len(bullet_rows) >= 2
                       and max(starts[i].box.x for i in bullet_rows)-min(starts[i].box.x for i in bullet_rows) < 1
                       and len({starts[i].char_unicode for i in bullet_rows}) == 1
                       and max(starts[i].pdf_style.font_size for i in bullet_rows)-min(starts[i].pdf_style.font_size for i in bullet_rows) <= .02)
        boundaries = ([i for i, _ in numeric] if is_numbered else
                      [i for i, _ in letters] if is_lettered else
                      bullet_rows if is_bulleted else authors if is_references else [])
        markers = dict(numeric if is_numbered else letters if is_lettered else [])
        markers.update((i, legacy_bullets[id(starts[i])]) for i in bullet_rows
                       if id(starts[i]) in legacy_bullets)
        # Already separated bullets still need their original hanging column.
        is_bullet = bool(re.match(r'^[•▪◦●]\s*', texts[0]) or id(starts[0]) in legacy_bullets)
        # A source item already separated by the layout detector still needs
        # its hanging indentation. Require an explicit label and aligned body
        # continuation rows, rather than treating an arbitrary leading number
        # as a one-item list.
        single_marker = _entry_marker(rows[0]) or _entry_marker(rows[0], letter=True)
        is_single = bool(single_marker and single_marker['explicit'] and len(rows) > 1 and
                         all(abs(c.box.x - single_marker['body']) < 1 for c in starts[1:]))
        if not boundaries and not is_bullet and not is_single:
            result.append(paragraph)
            continue
        boundaries = sorted(set([0] + boundaries + [len(rows)]))
        for start, end in zip(boundaries, boundaries[1:]):
            item = paragraph if len(boundaries) == 2 else copy.copy(paragraph)
            item.pdf_paragraph_composition = [PdfParagraphComposition(pdf_line=PdfLine(pdf_character=row))
                                              for row in rows[start:end]]
            for comp in item.pdf_paragraph_composition:
                finder.update_line_data(comp.pdf_line)
            finder.update_paragraph_data(item, update_unicode=True)
            item.first_line_indent = False
            if item.debug_id and item is not paragraph:
                item.debug_id += '-entry-' + str(start)
            candidate_indents = [c.box.x - starts[start].box.x for c in starts[start + 1:end]
                                 if size * .4 <= c.box.x - starts[start].box.x <= size * 3]
            indent = min(candidate_indents) if candidate_indents else 0
            if not indent:
                marker = markers.get(start) or (single_marker if start == 0 and is_single else None)
                if marker:
                    indent = marker['body'] - starts[start].box.x
            if indent > 0 and indent < (item.box.x2 - item.box.x) * .3:
                _hanging_indents[id(item)] = (item, indent)
                hanging += 1
            result.append(item)
        splits += max(0, len(boundaries) - 2)
    page.pdf_paragraph = result
    if splits or hanging:
        with _lock:
            _counters['restoredStructuredEntryBoundaries'] += splits
            _counters['registeredHangingIndents'] += hanging
    return splits


def _physical_line(line):
    chars = line.pdf_character if line else []
    visible = [c for c in chars if isinstance(c.char_unicode, str) and c.char_unicode.strip()]
    if not visible or any(c.vertical or not c.pdf_style or not c.box or not c.visual_bbox
                          or not c.visual_bbox.box or not isinstance(c.pdf_style.font_size, (int, float))
                          or c.pdf_style.font_size <= 0
                          or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                                 for b in (c.box, c.visual_bbox.box) for v in (b.x,b.y,b.x2,b.y2))
                          or not isinstance(c.char_unicode, str) for c in chars):
        return None
    size = max(c.pdf_style.font_size for c in visible)
    if (max(c.box.y for c in visible)-min(c.box.y for c in visible) > size*0.25
            or any(b.box.x+0.25 < a.box.x for a,b in zip(visible,visible[1:]))):
        return None
    return chars, visible, size


_COPYRIGHT_START = re.compile(r'^\s*(?:Copyright\s*:|©\s*(?:19|20)\d{2})', re.I)
_COPYRIGHT_LICENSE = re.compile(
    r'Creative\s+Commons|all\s+rights\s+reserved|open[ -]access.*licen[cs]e', re.I)


def preserve_copyright_blocks(page):
    """Keep one uniquely continuous, explicitly anchored license block intact.

    Layout detectors may divide a physical word across a fallback fragment and
    a translated paragraph. Gather only adjacent, equal-sized source lines of
    a Copyright/© block; require license wording and a closed final sentence.
    No glyph is synthesized, rewritten, or discarded.
    """
    records = []
    for paragraph in page.pdf_paragraph:
        if paragraph.vertical or paragraph.layout_label not in {'plain text', 'text', 'fallback_line'}:
            continue
        infos = [_physical_line(c.pdf_line) for c in paragraph.pdf_paragraph_composition]
        if not infos or any(info is None for info in infos):
            continue
        for info in infos:
            chars, visible, size = info
            if (any(abs(c.pdf_style.font_size-size) > 0.02 for c in visible)
                    or any(c.xobj_id != paragraph.xobj_id for c in visible)):
                continue
            records.append(dict(paragraph=paragraph, chars=chars, size=size,
                                x=visible[0].box.x, right=visible[-1].box.x2,
                                y=visible[0].box.y,
                                text=''.join(c.char_unicode for c in chars)))
    removed, blocks, count = set(), 0, 0
    for anchor in records:
        if id(anchor['paragraph']) in removed or not _COPYRIGHT_START.match(anchor['text']):
            continue
        size, left = anchor['size'], anchor['x']
        allowed_fonts = {c.pdf_style.font_id for c in anchor['chars']}
        selected, rows, current = [], [], anchor
        complete = False
        for _ in range(4):
            row = [current]
            while True:
                end = row[-1]
                adjacent = [r for r in records if r is not end and r not in row
                    and r not in selected and id(r['paragraph']) not in removed
                    and r['paragraph'].xobj_id == anchor['paragraph'].xobj_id
                    and abs(r['size']-size) <= 0.02 and abs(r['y']-end['y']) <= size*0.1
                    and -0.25 <= r['x']-end['right'] <= size*0.75
                    and {c.pdf_style.font_id for c in r['chars']} <= allowed_fonts]
                if len(adjacent) != 1:
                    if adjacent:  # An overlapping/ambiguous continuation is unsafe.
                        row = []
                    break
                row.append(adjacent[0])
            if not row:
                break
            selected.extend(row)
            rows.append(''.join(r['text'] for r in row).strip())
            text = ' '.join(rows)
            if _COPYRIGHT_LICENSE.search(text) and re.search(r'[.!]$', rows[-1]):
                complete = True
                break
            next_rows = [r for r in records if r not in selected
                and id(r['paragraph']) not in removed
                and r['paragraph'].xobj_id == anchor['paragraph'].xobj_id
                and abs(r['size']-size) <= 0.02 and abs(r['x']-left) <= max(1, size*0.2)
                and size*0.9 <= current['y']-r['y'] <= size*1.7
                and {c.pdf_style.font_id for c in r['chars']} <= allowed_fonts]
            if len(next_rows) != 1:
                break
            current = next_rows[0]
        if not complete:
            continue
        paragraphs = {id(r['paragraph']): r['paragraph'] for r in selected}
        glyphs = [c for p in paragraphs.values() for comp in p.pdf_paragraph_composition
                  for c in comp.pdf_line.pdf_character]
        selected_ids = {id(c) for r in selected for c in r['chars']}
        glyph_ids = [id(c) for c in glyphs]
        if (set(glyph_ids) != selected_ids or len(set(glyph_ids)) != len(glyph_ids)
                or set(glyph_ids) & {id(c) for c in page.pdf_character}):
            continue  # Never consume a partial paragraph or duplicate a glyph.
        page.pdf_character.extend(glyphs)
        removed.update(paragraphs)
        blocks += 1
        count += len(glyphs)
    if removed:
        page.pdf_paragraph = [p for p in page.pdf_paragraph if id(p) not in removed]
        with _lock:
            _counters['preservedCopyrightBlocks'] += blocks
            _counters['preservedCopyrightParagraphs'] += len(removed)
            _counters['preservedCopyrightGlyphs'] += count
    return blocks


def merge_caption_closers(finder, page):
    """Join a detached ')' only to its unique adjacent, unclosed caption line."""
    removed = set()
    for closing in list(page.pdf_paragraph):
        if (closing.layout_label not in {'figure_caption', 'table_caption'} or closing.vertical
                or len(closing.pdf_paragraph_composition) != 1):
            continue
        end_line = closing.pdf_paragraph_composition[0].pdf_line
        end = _physical_line(end_line)
        if not end or len(end[1]) != 1 or end[1][0].char_unicode not in {')', '）'}:
            continue
        symbol = end[1][0]
        opener = '(' if symbol.char_unicode == ')' else '（'
        candidates = []
        for body in page.pdf_paragraph:
            if (body is closing or id(body) in removed or body.vertical
                    or body.layout_label != closing.layout_label or body.xobj_id != closing.xobj_id
                    or not body.pdf_paragraph_composition
                    or any(comp.pdf_line is None for comp in body.pdf_paragraph_composition)):
                continue
            last_line = body.pdf_paragraph_composition[-1].pdf_line
            info = _physical_line(last_line)
            if not info:
                continue
            text = ''.join(c.char_unicode for comp in body.pdf_paragraph_composition
                           for c in comp.pdf_line.pdf_character)
            # Exactly one unmatched opener of this kind; no unmatched closers.
            depth, malformed = 0, False
            for ch in text:
                if ch == opener:
                    depth += 1
                elif ch == symbol.char_unicode:
                    depth -= 1
                    malformed |= depth < 0
            if depth != 1 or malformed:
                continue
            anchor = info[1][-1]
            size = symbol.pdf_style.font_size
            gap = symbol.box.x-anchor.box.x2
            if (anchor.pdf_style.font_id != symbol.pdf_style.font_id
                    or abs(anchor.pdf_style.font_size-size) > 0.01
                    or abs(anchor.box.y-symbol.box.y) > size*0.1
                    or not -0.25 <= gap <= size*0.4
                    or anchor.xobj_id != symbol.xobj_id):
                continue
            if any(id(c) in {id(g) for g in info[0]} for c in end[0]):
                continue
            candidates.append((body,last_line))
        if len(candidates) != 1:
            continue
        body,line = candidates[0]
        line.pdf_character.extend(end[0])
        finder.update_line_data(line)
        finder.update_paragraph_data(body, update_unicode=True)
        removed.add(id(closing))
    if removed:
        page.pdf_paragraph = [p for p in page.pdf_paragraph if id(p) not in removed]
        with _lock:
            _counters['mergedCaptionClosers'] += len(removed)
    return len(removed)


def merge_caption_fragments(finder, page):
    """Join uniquely adjacent detector fragments of one physical caption.

    Recognize only an explicit Figure/Table number prefix and a tiny trailing
    alphabetic word fragment; same font, size, baseline and sub-space gap are
    required. A nearby label, another line or an ambiguous neighbor is kept.
    """
    removed = set()
    for body in page.pdf_paragraph:
        # A detector can label a numbered caption as a table footnote. Open
        # only its tiny detached word ending, never arbitrary footnote bodies
        # or a prefix candidate inferred from nearby text.
        first_line = (body.pdf_paragraph_composition[0].pdf_line
                      if body.pdf_paragraph_composition else None)
        numbered_footnote = (body.layout_label == 'table_footnote' and first_line is not None
                            and bool(re.match(r'^\s*(?:Fig(?:ure)?\.?|Table)\s+\d+[a-z]?[.:]\s+[A-Z]',
                                              ''.join(c.char_unicode or '' for c in first_line.pdf_character), re.I)))
        if (body.vertical or (body.layout_label not in {'figure_caption', 'table_caption'} and not numbered_footnote)
                or not body.pdf_paragraph_composition
                or any(c.pdf_line is None for c in body.pdf_paragraph_composition)):
            continue
        for side in ('prefix', 'suffix'):
            if numbered_footnote and side == 'prefix':
                continue
            comp = body.pdf_paragraph_composition[0 if side == 'prefix' else -1]
            info = _physical_line(comp.pdf_line)
            if not info:
                continue
            chars, visible, _ = info
            anchor = visible[0 if side == 'prefix' else -1]
            body_text = ''.join(c.char_unicode for c in chars)
            if (side == 'prefix' and not re.match(r'^\s*(?:[.:]|[A-Za-z])', body_text)
                    or side == 'suffix' and not re.search(r'[A-Za-z]$', body_text)):
                continue
            candidates = []
            for fragment in page.pdf_paragraph:
                if (fragment is body or id(fragment) in removed or fragment.vertical
                        or fragment.layout_label != 'fallback_line' or fragment.xobj_id != body.xobj_id
                        or len(fragment.pdf_paragraph_composition) != 1):
                    continue
                line = fragment.pdf_paragraph_composition[0].pdf_line
                candidate = _physical_line(line)
                if not candidate:
                    continue
                text = ''.join(c.char_unicode for c in candidate[0]).strip()
                complete_prefix = bool(re.fullmatch(r'(?:Fig(?:ure)?\.?|Table)\s+\d+[a-z]?[.:]', text, re.I))
                if (side == 'prefix' and not (complete_prefix or re.fullmatch(
                        r'(?:Fig(?:ure)?\.?|Table)\s+\d+[a-z]?', text, re.I)
                        and re.match(r'^\s*[.:]', body_text))
                        or side == 'suffix' and not re.fullmatch(r'[a-z]{1,3}\.?', text)):
                    continue
                last = candidate[1][-1] if side == 'prefix' else anchor
                first = anchor if side == 'prefix' else candidate[1][0]
                size = anchor.pdf_style.font_size
                # A visibly measured word space must not be reinterpreted as a
                # detached suffix solely because the block starts Figure/Table.
                maximum_gap = size * (.08 if numbered_footnote else .6 if side == 'prefix' and complete_prefix else .3)
                if (any((c.pdf_style.font_id != anchor.pdf_style.font_id and not (side == 'prefix' and complete_prefix))
                        or abs(c.pdf_style.font_size - size) > .02
                        or abs(c.box.y - anchor.box.y) > size * .1
                        or c.xobj_id != anchor.xobj_id for c in candidate[1])
                        or not -.25 <= first.box.x - last.box.x2 <= maximum_gap
                        or numbered_footnote and any(c.formula_layout_id is not None
                                                   for c in (anchor, *candidate[1]))):
                    continue
                candidates.append((fragment, candidate[0]))
            if len(candidates) != 1:
                continue
            fragment, incoming = candidates[0]
            if side == 'prefix':
                comp.pdf_line.pdf_character = incoming + chars
            else:
                comp.pdf_line.pdf_character.extend(incoming)
            finder.update_line_data(comp.pdf_line)
            finder.update_paragraph_data(body, update_unicode=True)
            removed.add(id(fragment))
    if removed:
        page.pdf_paragraph = [p for p in page.pdf_paragraph if id(p) not in removed]
        with _lock:
            _counters['mergedCaptionFragments'] += len(removed)
    return len(removed)


def merge_source_terminal_punctuation(finder, page):
    """Attach one source period/comma touching the preceding prose word.

    The original single glyph must be the immediate next paragraph and share
    its predecessor's native baseline, font, size and XObject. No other source
    glyph may occupy its visual position; numbers, formulas, separate lines,
    captions and arbitrary punctuation sequences never qualify.
    """
    paragraphs = list(page.pdf_paragraph)
    all_chars = list(page.pdf_character) + [c for p in paragraphs for co in p.pdf_paragraph_composition
                                           for c in _composition_characters(co)]
    occurrences = Counter(map(id, all_chars))
    removed = set()
    for body, fragment in zip(paragraphs, paragraphs[1:]):
        if (id(body) in removed or body.vertical or fragment.vertical
                or body.layout_label not in {'text', 'plain text'}
                or fragment.layout_label not in {'text', 'plain text', 'fallback_line'}
                or body.xobj_id != fragment.xobj_id
                or len(fragment.pdf_paragraph_composition) != 1
                or not body.pdf_paragraph_composition
                or any(co.pdf_line is None for co in body.pdf_paragraph_composition)
                or len(re.findall(r'[A-Za-z]{2,}', body.unicode or '')) < 4):
            continue
        line = body.pdf_paragraph_composition[-1].pdf_line
        left = _physical_line(line)
        right = _physical_line(fragment.pdf_paragraph_composition[0].pdf_line)
        if not left or not right or len(right[0]) != 1:
            continue
        marker, anchor = right[0][0], left[1][-1]
        tail = re.search(r'[a-z]{3,}$', ''.join(c.char_unicode for c in left[0]))
        size = anchor.pdf_style.font_size
        if (not tail or marker.char_unicode not in {'.', ','}
                or marker.pdf_character_id is None or anchor.pdf_character_id is None
                or marker.formula_layout_id is not None or anchor.formula_layout_id is not None
                or marker.pdf_style.font_id != anchor.pdf_style.font_id
                or abs(marker.pdf_style.font_size-size) > .02
                or marker.xobj_id != anchor.xobj_id or marker.xobj_id != body.xobj_id
                or abs(marker.box.y-anchor.box.y) > .05*size
                or not -.03*size <= marker.box.x-anchor.box.x2 <= .08*size
                or occurrences[id(marker)] != 1 or occurrences[id(anchor)] != 1):
            continue
        visual = marker.visual_bbox.box
        if any(c is not marker and c is not anchor and _finite_character(c)
               and (c.char_unicode or '').strip() and c.xobj_id == marker.xobj_id
               and abs(c.box.y-marker.box.y) <= .1*size
               and c.visual_bbox.box.x < visual.x2 and c.visual_bbox.box.x2 > visual.x
               for c in all_chars):
            continue
        line.pdf_character.append(marker)
        finder.update_line_data(line)
        finder.update_paragraph_data(body, update_unicode=True)
        removed.add(id(fragment))
    if removed:
        page.pdf_paragraph = [p for p in page.pdf_paragraph if id(p) not in removed]
        with _lock:
            _counters['mergedSourceTerminalPunctuation'] += len(removed)
    return len(removed)


def merge_source_year_range_fragments(finder, page):
    """Restore a source line split around a separately detected year-range dash.

    Only three consecutive source paragraphs may participate: a one-line prose
    prefix ending in '(YYYY', one original dash glyph, and a body paragraph
    beginning with 'YY)' or 'YYYY)'. The entire boundary line must share its
    baseline, face, size and XObject; every range glyph must touch its next
    neighbor within a fraction of a space. No text/glyph is created, sorted or
    guessed, and the remainder of the body keeps its existing compositions.
    """
    paragraphs = list(page.pdf_paragraph)
    page_chars = list(page.pdf_character)
    page_chars.extend(c for p in paragraphs for comp in p.pdf_paragraph_composition
                      for c in _composition_characters(comp))
    occurrences = Counter(id(c) for c in page_chars)
    removed, merged = set(), 0
    for left, separator, right in zip(paragraphs, paragraphs[1:], paragraphs[2:]):
        if (any(id(p) in removed or p.vertical for p in (left, separator, right))
                or left.layout_label not in {'plain text', 'text'}
                or right.layout_label != left.layout_label
                or separator.layout_label not in {'plain text', 'text', 'title', 'fallback_line'}
                or len(left.pdf_paragraph_composition) != 1
                or len(separator.pdf_paragraph_composition) != 1
                or not right.pdf_paragraph_composition
                or len({p.xobj_id for p in (left, separator, right)}) != 1):
            continue
        line_parts = [_physical_line(p.pdf_paragraph_composition[0].pdf_line)
                      for p in (left, separator, right)]
        if any(info is None for info in line_parts):
            continue
        first, middle, last = [info[0] for info in line_parts]
        left_text = ''.join(c.char_unicode for c in first)
        right_text = ''.join(c.char_unicode for c in last)
        opening = re.search(r'\(([12]\d{3})$', left_text)
        closing = re.match(r'(\d{2}|[12]\d{3})\)\s*[A-Za-z]', right_text)
        if (not opening or not closing or len(re.findall(r'[A-Za-z]{2,}', left_text[:opening.start()])) < 3
                or len(middle) != 1 or middle[0].char_unicode not in {'–', '-'}):
            continue
        start_year, ending = int(opening[1]), closing[1]
        end_year = int(ending) if len(ending) == 4 else start_year // 100 * 100 + int(ending)
        if not start_year <= end_year <= start_year + 100:
            continue
        range_chars = first[-5:] + middle + last[:len(ending) + 1]
        if ''.join(c.char_unicode for c in range_chars) != opening[0] + middle[0].char_unicode + ending + ')':
            continue  # The range must map one-to-one to original glyphs.
        anchor = middle[0]
        size = anchor.pdf_style.font_size
        boundary_chars = first + middle + last
        visible = [c for c in boundary_chars if c.char_unicode.strip()]
        if (any(c.pdf_style.font_id != anchor.pdf_style.font_id
                or abs(c.pdf_style.font_size - size) > .02
                or abs(c.box.y - anchor.box.y) > size * .05
                or c.xobj_id != anchor.xobj_id or c.xobj_id != left.xobj_id
                or c.formula_layout_id is not None for c in visible)
                or any(c.pdf_character_id is None or len(c.char_unicode) != 1
                       or c.box.x2 <= c.box.x or occurrences[id(c)] != 1 for c in range_chars)
                or any(not -size * .03 <= b.box.x - a.box.x2 <= size * .12
                       for a, b in zip(range_chars, range_chars[1:]))):
            continue
        member_ids = {id(c) for c in boundary_chars}
        if (len(member_ids) != len(boundary_chars)
                or any(_finite_character(c) and isinstance(c.char_unicode, str) and c.char_unicode.strip()
                       and id(c) not in member_ids and c.xobj_id == anchor.xobj_id
                       and abs(c.box.y - anchor.box.y) <= size * .25
                       and c.box.x < range_chars[-1].box.x2 and c.box.x2 > range_chars[0].box.x
                       for c in page_chars)):
            continue  # An intervening/overprinted source glyph makes adjacency ambiguous.
        line = right.pdf_paragraph_composition[0].pdf_line
        line.pdf_character = boundary_chars
        finder.update_line_data(line)
        finder.update_paragraph_data(right, update_unicode=True)
        removed.update((id(left), id(separator)))
        merged += 1
    if merged:
        page.pdf_paragraph = [p for p in page.pdf_paragraph if id(p) not in removed]
        with _lock:
            _counters['mergedSourceYearRanges'] += merged
    return merged


_LINE_SI_UNIT = re.compile(r'(?:[pnumkMGTμµ]?(?:Ω|F|H|V|A|W|Hz|s|m|J|K))\Z')


def _technical_physical_rows(line):
    """Recover clear physical rows inside an upstream merged PdfLine.

    A smaller single-character subscript is not a new baseline. Require its
    original same-font base immediately to the left; all other visible glyphs
    must follow one main baseline, or reset left on a distinct lower row.
    """
    chars = line.pdf_character if line else []
    visible = [c for c in chars if isinstance(c.char_unicode, str) and c.char_unicode.strip()]
    if not visible or any(c.vertical or not c.pdf_style or not c.box or not c.visual_bbox
                          or not c.visual_bbox.box or not isinstance(c.pdf_style.font_size, (int, float))
                          or c.pdf_style.font_size <= 0
                          or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                                 for b in (c.box, c.visual_bbox.box) for v in (b.x,b.y,b.x2,b.y2))
                          or not isinstance(c.char_unicode, str) for c in chars):
        return None
    size = max(c.pdf_style.font_size for c in visible)
    rows, current, row_visible = [], [], []
    anchor = None
    for char in chars:
        if not char.char_unicode.strip():
            current.append(char)
            continue
        small = char.pdf_style.font_size < size*0.85
        if small:
            previous = row_visible[-1] if row_visible else None
            if (not previous or previous.pdf_style.font_size < size*0.85
                    or not re.fullmatch(r'[A-Za-z0-9]',char.char_unicode)
                    or not re.fullmatch(r'[A-Za-z]',previous.char_unicode)
                    or previous.pdf_style.font_id != char.pdf_style.font_id
                    or previous.xobj_id != char.xobj_id
                    or not -0.25 <= char.box.x-previous.box.x2 <= size*0.25
                    or not -size*0.6 <= char.box.y-previous.box.y <= -size*0.1):
                return None
        elif anchor is None:
            anchor = char
        elif abs(char.box.y-anchor.box.y) > size*0.25:
            if (not 0.9*size <= anchor.box.y-char.box.y <= 2.5*size
                    or abs(char.box.x-anchor.box.x) > max(1,size*0.15)):
                return None
            rows.append((current,row_visible,size))
            current,row_visible,anchor = [],[],char
        if row_visible and char.box.x+0.25 < row_visible[-1].box.x:
            return None
        current.append(char)
        row_visible.append(char)
    if not row_visible:
        return None
    rows.append((current,row_visible,size))
    return rows


def _standalone_technical_row(info):
    # Validated small subscripts are retained as original glyphs, but excluded
    # from lexical checks so U + small o is not mistaken for an English word.
    text = ''.join(c.char_unicode for c in info[0]
                   if c.pdf_style.font_size >= info[2]*0.85).strip()
    if ('=' not in text and len(re.findall(r'[<>≤≥]',text)) < 2) or not re.search(r'\d',text):
        return None
    tokens = re.findall(r'[A-Za-zα-ωΑ-Ωµ]+', text)
    if (not any(_LINE_SI_UNIT.fullmatch(token) for token in tokens)
            or any(not (_LINE_SI_UNIT.fullmatch(token) or re.fullmatch(r'[A-Za-zα-ωΑ-Ω]',token))
                   for token in tokens)):
        return None
    if not re.fullmatch(r'[\s\d.,:;()\[\]=+−\-×÷*/<>≤≥≈±∓·∗^²³°]*',
                        re.sub(r'[A-Za-zα-ωΑ-Ωµ]+','',text)):
        return None
    return info


def preserve_trailing_equations(finder, page):
    """Keep two or more standalone calculation/inequality rows below prose."""
    paragraphs, lines, glyphs = 0, 0, 0
    for paragraph in page.pdf_paragraph:
        comps = paragraph.pdf_paragraph_composition
        if (paragraph.vertical or paragraph.layout_label not in {'plain text','text'}
                or len(comps) < 2 or any(c.pdf_line is None for c in comps)):
            continue
        split = len(comps)
        tail_info = []
        while split:
            rows = _technical_physical_rows(comps[split-1].pdf_line)
            if not rows or any(not _standalone_technical_row(row) for row in rows):
                break
            tail_info = rows+tail_info
            split -= 1
        if split == 0 or len(tail_info) < 2:
            continue
        head = [c for comp in comps[:split] for c in comp.pdf_line.pdf_character]
        if not re.search(r'[A-Za-z]{3,}', ''.join(c.char_unicode for c in head)):
            continue
        head_last = _physical_line(comps[split-1].pdf_line)
        if not head_last:
            continue
        sequence = [head_last]+tail_info
        if any(not 0.9*max(a[2],b[2]) <= a[1][0].box.y-b[1][0].box.y <= 2.5*max(a[2],b[2])
               for a,b in zip(sequence,sequence[1:])):
            continue
        if any(abs(a[1][0].box.x-b[1][0].box.x)>max(1,a[2]*0.15)
               for a,b in zip(tail_info,tail_info[1:])):
            continue
        tail = [c for info in tail_info for c in info[0]]
        ids = [id(c) for c in head+tail]
        if len(set(ids)) != len(ids) or set(ids)&{id(c) for c in page.pdf_character}:
            continue
        barrier = max(c.box.y2 for c in tail)+0.01
        visible_head = [c for c in head if c.char_unicode.strip()]
        if min(c.visual_bbox.box.y for c in visible_head) <= barrier:
            continue
        paragraph.pdf_paragraph_composition = comps[:split]
        finder.update_paragraph_data(paragraph, update_unicode=True)
        # Empty space boxes can extend below actual ink. Keep the prose box
        # above the preserved rows, which also block typesetter expansion.
        paragraph.box.y = max(paragraph.box.y,barrier)
        page.pdf_character.extend(tail)
        paragraphs += 1
        lines += len(tail_info)
        glyphs += len(tail)
    if paragraphs:
        with _lock:
            _counters['preservedTrailingEquationParagraphs'] += paragraphs
            _counters['preservedTrailingEquationLines'] += lines
            _counters['preservedTrailingEquationGlyphs'] += glyphs
    return lines


def preserve_alphabetical_index(page):
    """Preserve a confidently recognized alphabetical index as source glyphs.

    Merged multi-line entries can contain several page references; translating
    them as prose loses references, dot leaders and even isolated letter titles.
    Require repeated alphabetic headings AND a dense set of dotted references.
    A normal contents page, bibliography or prose page must not match. Moving
    original character objects to the existing page character lane also avoids
    re-typesetting short headings that upstream skips during translation.
    """
    paragraphs = page.pdf_paragraph
    if not paragraphs:
        return False
    headings, reference_lines, meaningful_lines = set(), 0, 0
    glyphs = []
    for paragraph in paragraphs:
        if paragraph.vertical:
            return False
        lines = []
        for composition in paragraph.pdf_paragraph_composition:
            if composition.pdf_line is None:
                return False  # Never discard an unfamiliar composition.
            chars = composition.pdf_line.pdf_character
            if any(not isinstance(c.char_unicode, str) or c.vertical for c in chars):
                return False
            lines.append("".join(c.char_unicode for c in chars))
            glyphs.extend(chars)
        text = " ".join(lines).strip()
        if str(paragraph.layout_label).casefold() == "title" and re.fullmatch(r"[A-Z]", text):
            headings.add(text)
        for line in lines:
            if re.search(r"[A-Za-z]", line):
                meaningful_lines += 1
            if re.search(r"[A-Za-z].*\.{8,}\s*[1-9][0-9]{0,3}(?:\s*,\s*[1-9][0-9]{0,3})*\s*$", line):
                reference_lines += 1
        # An introductory paragraph of real prose is not an index entry. Avoid
        # preserving an entire mixed page just because it contains an index box.
        if "." * 8 not in text and len(re.findall(r"[A-Za-z]{2,}", text)) > 28:
            return False
    if len(headings) < 3 or reference_lines < 8 or reference_lines < meaningful_lines * 0.25:
        return False
    existing_ids = {id(c) for c in page.pdf_character}
    if len({id(c) for c in glyphs}) != len(glyphs) or any(id(c) in existing_ids for c in glyphs):
        return False
    page.pdf_character.extend(glyphs)
    page.pdf_paragraph = []
    with _lock:
        _counters["preservedIndexPages"] += 1
        _counters["preservedIndexGlyphs"] += len(glyphs)
        _counters["preservedIndexReferenceLines"] += reference_lines
    return True


def _toc_candidate(paragraph):
    """Return a split only for a single physical line with an explicit leader."""
    compositions = paragraph.pdf_paragraph_composition
    if paragraph.vertical or len(compositions) != 1 or not compositions[0].pdf_line:
        return None
    line = compositions[0].pdf_line
    chars = line.pdf_character
    if not chars or any(not isinstance(c.char_unicode, str) or not c.char_unicode
                        for c in chars):
        return None
    text = "".join(c.char_unicode for c in chars)
    match = _TOC_TAIL.search(text)
    if match is None or int(match[1]) < 1:
        return None
    # A source glyph can represent a complete ligature (for example "fi").
    # Match offsets refer to Unicode, not glyph indexes. Only split at actual
    # glyph boundaries; never divide a ligature or synthesize its characters.
    boundaries, offset = {0: 0}, 0
    for index, char in enumerate(chars):
        offset += len(char.char_unicode)
        boundaries[offset] = index + 1
    if any(offset not in boundaries for offset in
           (match.start(), match.start(1), match.end(1))):
        return None
    leader_index = boundaries[match.start()]
    if any(len(c.char_unicode) != 1 for c in chars[leader_index:]):
        return None
    label = text[:match.start()]
    if len(re.findall(r"[^\W\d_]", label, flags=re.UNICODE)) < 2:
        return None
    if any(c.box is None or c.visual_bbox is None or c.visual_bbox.box is None
           or c.pdf_style is None or not isinstance(c.pdf_style.font_size, (int, float))
           or c.pdf_style.font_size <= 0
           or any(not isinstance(value, (int, float)) or not math.isfinite(value)
                  for box in (c.box, c.visual_bbox.box)
                  for value in (box.x, box.y, box.x2, box.y2)) for c in chars):
        return None
    # Reject merged lines, vertical writing and ambiguous content-stream order.
    size = max(c.pdf_style.font_size for c in chars)
    if (max(c.box.y for c in chars) - min(c.box.y for c in chars) > size * 0.4
            or any(c.vertical for c in chars)
            or any(b.box.x + 0.25 < a.box.x for a, b in zip(chars, chars[1:]))):
        return None
    # Some source spaces have boxes extending into the first dot. Keep those
    # original spacing glyphs with the leader instead of treating their empty
    # ink as overlapping body text. Visible text must still clear the leader.
    body_end = leader_index
    while body_end and re.fullmatch(r"[ \t]+", chars[body_end - 1].char_unicode):
        body_end -= 1
    body, tail = chars[:body_end], chars[body_end:]
    if not body:
        return None
    leader_x = chars[leader_index].box.x
    body_right = max(c.visual_bbox.box.x2 for c in body)
    if body_right > leader_x + 0.25 or leader_x - min(c.box.x for c in body) < size:
        return None
    digits = chars[boundaries[match.start(1)]:boundaries[match.end(1)]]
    return {"paragraph": paragraph, "line": line, "body": body, "tail": tail,
            "leaderX": leader_x, "right": max(c.box.x2 for c in digits),
            "baseline": chars[0].box.y}


def preserve_toc_leaders(finder, page):
    """Keep leader/page-number glyphs outside the translation/reflow pipeline.

    Require another matching row with an aligned page-number edge. This avoids
    mistaking a lone prose ellipsis followed by a number for a table of contents.
    The existing page-level character lane preserves source glyphs and positions;
    Typesetting.get_max_right_space also treats these characters as obstacles.
    """
    candidates = [candidate for p in page.pdf_paragraph
                  if (candidate := _toc_candidate(p)) is not None]
    confirmed = [candidate for candidate in candidates if any(
        other is not candidate and abs(other["right"] - candidate["right"]) <= 2
        and abs(other["baseline"] - candidate["baseline"]) > 2
        for other in candidates)]
    for candidate in confirmed:
        paragraph, line = candidate["paragraph"], candidate["line"]
        line.pdf_character = candidate["body"]
        finder.update_line_data(line)
        finder.update_paragraph_data(paragraph, update_unicode=True)
        paragraph.box.x2 = min(paragraph.box.x2, candidate["leaderX"])
        # Move existing objects, including source render order and empty spacing
        # glyphs. Do not paint replacement dots or ask the model to regenerate them.
        page.pdf_character.extend(candidate["tail"])
    if confirmed:
        with _lock:
            _counters["preservedTOCRows"] += len(confirmed)
            _counters["preservedTOCGlyphs"] += sum(
                c.pdf_character_id is not None for candidate in confirmed for c in candidate["tail"])
            _counters["pagesWithPreservedTOC"] += 1
    return len(confirmed)


def install():
    """Install in the actual translation subprocess before parsing pages."""
    global _installed, _original_process_page, _original_is_formulas_font
    global _original_process_translatable_formulas, _original_is_translatable_formula
    global _original_process_comma_formulas, _original_should_split_formula
    global _original_styles_process_page, _original_layout_typesetting_units
    global _original_find_optimal_scale_and_layout
    with _lock:
        if _installed:
            return
        from importlib.metadata import version
        if version("babeldoc") != "0.6.2":
            raise RuntimeError("Layout guard requires dependency re-audit")
        from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
        from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
        from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
        from babeldoc.format.pdf.document_il.utils import formular_helper
        _original_is_formulas_font = formular_helper.is_formulas_font
        formular_helper.is_formulas_font = _guard_is_formulas_font
        _original_process_translatable_formulas = StylesAndFormulas.process_translatable_formulas
        _original_is_translatable_formula = StylesAndFormulas.is_translatable_formula
        StylesAndFormulas.process_translatable_formulas = _guard_process_translatable_formulas
        StylesAndFormulas.is_translatable_formula = _guard_is_translatable_formula
        _original_process_comma_formulas = StylesAndFormulas.process_comma_formulas
        _original_should_split_formula = StylesAndFormulas.should_split_formula
        StylesAndFormulas.process_comma_formulas = _guard_process_comma_formulas
        StylesAndFormulas.should_split_formula = _guard_should_split_formula
        _original_styles_process_page = StylesAndFormulas.process_page
        StylesAndFormulas.process_page = _guard_styles_process_page
        _original_find_optimal_scale_and_layout = Typesetting._find_optimal_scale_and_layout
        Typesetting._find_optimal_scale_and_layout = _guard_find_optimal_scale_and_layout
        _original_layout_typesetting_units = Typesetting._layout_typesetting_units
        Typesetting._layout_typesetting_units = _guard_layout_typesetting_units
        _original_process_page = ParagraphFinder.process_page
        ParagraphFinder.process_page = _guard_process_page
        _installed = True


def snapshot():
    with _lock:
        return {"guardVersion": GUARD_VERSION, "guardInstalled": _installed,
                "repairedSubscriptOffsets": _counters['repairedSubscriptOffsets'],
                "boundSourceAccentedNames": _counters['boundSourceAccentedNames'],
                "repairedSourceOverprintedWords": _counters['repairedSourceOverprintedWords'],
                "restoredSourceReferenceArticles": _counters['restoredSourceReferenceArticles'],
                "repairedScriptFollowingFormulaOffsets": _counters['repairedScriptFollowingFormulaOffsets'],
                "registeredScriptBindings": _counters['registeredScriptBindings'],
                "restoredPhysicalLineBreaks": _counters['restoredPhysicalLineBreaks'],
                "boundSourceFormulaDescriptors": _counters['boundSourceFormulaDescriptors'],
                "boundSourceFormulaDecimals": _counters['boundSourceFormulaDecimals'],
                "boundSourceSubscriptIndices": _counters['boundSourceSubscriptIndices'],
                "rejoinedSourcePageDefinitionHeads": _counters['rejoinedSourcePageDefinitionHeads'],
                "rejoinedSourceColumnSentenceTails": _counters['rejoinedSourceColumnSentenceTails'],
                "rejoinedSourceColumnOrphanHeads": _counters['rejoinedSourceColumnOrphanHeads'],
                "rejoinedSourceColumnURLPaths": _counters["rejoinedSourceColumnURLPaths"],
                "rejoinedSourceColumnShortHeads": _counters['rejoinedSourceColumnShortHeads'],
                "pairedSourceColumnSentences": _counters['pairedSourceColumnSentences'],
                "rejoinedSourcePageShortHeads": _counters['rejoinedSourcePageShortHeads'],
                "mergedSourceNumberedRuleTitles": _counters['mergedSourceNumberedRuleTitles'],
                "rejoinedSourceParentheticalComparisons": _counters['rejoinedSourceParentheticalComparisons'],
                "legacyMathFontMatches": _counters['legacyMathFontMatches'],
                "preservedLegacySymbolFormulas": _counters['preservedLegacySymbolFormulas'],
                "preservedLegacyCommaFormulas": _counters['preservedLegacyCommaFormulas'],
                "preservedCopyrightBlocks": _counters['preservedCopyrightBlocks'],
                "preservedCopyrightParagraphs": _counters['preservedCopyrightParagraphs'],
                "preservedCopyrightGlyphs": _counters['preservedCopyrightGlyphs'],
                "normalizedWebdingsBullets": _counters["normalizedWebdingsBullets"],
                "pagesWithNormalizedBullets": _counters["pagesWithNormalizedBullets"],
                "preservedTOCRows": _counters["preservedTOCRows"],
                "preservedTOCGlyphs": _counters["preservedTOCGlyphs"],
                "pagesWithPreservedTOC": _counters["pagesWithPreservedTOC"],
                "preservedIndexPages": _counters["preservedIndexPages"],
                "preservedIndexGlyphs": _counters["preservedIndexGlyphs"],
                "preservedIndexReferenceLines": _counters["preservedIndexReferenceLines"],
                "mergedCaptionClosers": _counters["mergedCaptionClosers"],
                "mergedCaptionFragments": _counters["mergedCaptionFragments"],
                "mergedSourceTerminalPunctuation": _counters['mergedSourceTerminalPunctuation'],
                "mergedSourceOpenGrammarBlocks": _counters['mergedSourceOpenGrammarBlocks'],
                "mergedSourceYearRanges": _counters["mergedSourceYearRanges"],
                "preservedTrailingEquationParagraphs": _counters["preservedTrailingEquationParagraphs"],
                "preservedTrailingEquationLines": _counters["preservedTrailingEquationLines"],
                "preservedTrailingEquationGlyphs": _counters["preservedTrailingEquationGlyphs"],
                "restoredStructuredEntryBoundaries": _counters["restoredStructuredEntryBoundaries"],
                "rejoinedSourceResearchPhrases": _counters["rejoinedSourceResearchPhrases"],
                "mergedSourceBulletPrepositions": _counters["mergedSourceBulletPrepositions"],
                "restoredDetachedListMarkers": _counters["restoredDetachedListMarkers"],
                "registeredHangingIndents": _counters["registeredHangingIndents"],
                "rawDocumentTextStored": False}
