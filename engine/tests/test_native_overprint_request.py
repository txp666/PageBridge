import copy
import unittest
from types import SimpleNamespace as NS
import translation_guard as guard
import layout_guard as layout
from test_native_word_groups import overprint_fixture,styler

def ti(text):return NS(unicode=text,placeholders=[],base_style=NS(font_size=8,font_id='F2'),_pagebridge_layout_label='plain_text')

class NativeOverprintRequestTests(unittest.TestCase):
    def bound(self):
        p,q,_,_,_=overprint_fixture(True)
        self.assertEqual(layout.bind_source_overprinted_words(styler(),p),1)
        original=ti('Earlier we used a method. In {v1}section {v2}{v3}wwe show how the model works.')
        guard._prepare_native_overprint_words(q,original)
        return q,original

    def test_only_request_spelling_changes_and_real_source_stays_exact(self):
        q,original=self.bound();text=original.unicode
        result=guard._source_review_prompt_view(original,text)
        self.assertEqual(result,text.replace('wwe','we'))
        self.assertEqual(original.unicode,text)
        self.assertEqual(guard._source_review_prompt_view(original,result),result)

    def test_no_native_binding_or_changed_source_or_paragraph_is_not_authorized(self):
        q,original=self.bound()
        self.assertEqual(guard._native_overprint_request_replacements(ti(original.unicode)),[])
        changed=copy.copy(original);changed.unicode+=' changed.'
        self.assertEqual(guard._native_overprint_request_replacements(changed),[])
        different=copy.deepcopy(q)
        self.assertEqual(layout.source_native_overprint_words(different),())
        q.pdf_paragraph_composition.pop()
        self.assertEqual(layout.source_native_overprint_words(q),())

    def test_plain_recovery_copy_retains_exact_full_source_binding(self):
        q,original=self.bound()
        reduced=ti(original.unicode)
        guard._copy_source_continuity(original,reduced)
        self.assertEqual(guard._source_review_prompt_view(reduced,reduced.unicode),original.unicode.replace('wwe','we'))
        changed=ti(original.unicode.replace('model','variable'))
        guard._copy_source_continuity(original,changed)
        self.assertEqual(guard._native_overprint_request_replacements(changed),[])

if __name__=='__main__':unittest.main()
