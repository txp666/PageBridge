"""Source-proven weather roles; standalone native examples without PDFs/network."""
from pathlib import Path
import sys
from copy import deepcopy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import translation_guard as g
from test_translation_guard import ILTranslator, FormulaPlaceholder, ParagraphTranslateTracker, translator, paragraph
from babeldoc.format.pdf.document_il import PdfStyle,PdfFormula,PdfCharacter,Box,VisualBbox

DAILY='The observations were taken twice daily at 00GMT and at 12GMT.'
OBJECTIVE='We select as an objective ABC classifier the neural network because it is a self-organizing feature map.'
TRAINING='We take a long training period of twenty years before to the one year test period.'
BIAS='The table shows persistence in the classes A and C, withstrong wind persistence, can be quite biased as a result of considering daily average winds.'
CAPTION='Table 9. Wind (XYZ6) prediction from the previous(5XYZ) five days data wind by Neural Networks.'
FORECAST='This gives an average error for wind direction equal to that given by a conventional wind forecast model.'
VECTOR='Our vector codification provides the wind direction. The classes correspond to zero daily average wind.'
WINDOW='There is another group: there are 142 days with different wind to any one of the five previous days.'


def ti(text,label='plain_text',formulas=()):
    holders=[];style=PdfStyle(font_id='F1',font_size=9)
    for index,word in enumerate(formulas,1):
        chars=[PdfCharacter(char_unicode=c,pdf_character_id=ord(c),box=Box(i*5,0,(i+1)*5,9),visual_bbox=VisualBbox(Box(i*5,0,(i+1)*5,9)),vertical=False,pdf_style=deepcopy(style),xobj_id=0)for i,c in enumerate(word)]
        holders.append(FormulaPlaceholder(index,PdfFormula(pdf_character=chars),'{v'+str(index)+'}',r'\{v'+str(index)+r'\}'))
    value=ILTranslator.TranslateInput(text,holders,style);value._pagebridge_layout_label=label
    return value


class WindSourceRolesTests(unittest.TestCase):
    def test_daily_total_is_source_proven_not_twice_per_time(self):
        x=ti(DAILY);self.assertEqual(len(g._source_daily_observation_times(x)),1)
        original=x.unicode;view=g._source_review_prompt_view(x,x.unicode)
        self.assertIn('total frequency of twice per day',view);self.assertEqual(x.unicode,original)
        self.assertEqual(g._source_review_prompt_view(x,view),view)
        g._check_weather_frequency_roles(x,'每日00GMT和12GMT共观测两次。')
        g._check_weather_frequency_roles(x,'不是00GMT和12GMT各观测两次，而是每天共两次。')
        with self.assertRaises(g.PlaceholderIntegrityError):g._check_weather_frequency_roles(x,'每日00GMT和12GMT各采集两次。')
        for source in (DAILY.replace('twice daily','twice each'),DAILY.replace('12GMT','00GMT'),DAILY.replace('12GMT','12UTC'),DAILY.replace('were taken','were not taken')):
            self.assertEqual(g._source_daily_observation_times(ti(source)),[])

    def test_objective_classifier_uses_method_evidence_not_a_name(self):
        x=ti(OBJECTIVE);self.assertTrue(g._source_objective_weather_classifier(x))
        g._check_weather_frequency_roles(x,'作为客观ABC分类器，网络是自组织特征映射。')
        with self.assertRaises(g.PlaceholderIntegrityError):g._check_weather_frequency_roles(x,'作为目标ABC分类器，网络是自组织特征映射。')
        self.assertFalse(g._source_objective_weather_classifier(ti(OBJECTIVE.replace('because it is a self-organizing feature map','for the experiment'))))
        self.assertFalse(g._source_objective_weather_classifier(ti(OBJECTIVE.replace('select','do not select'))))

    def test_training_chronology_not_coverage_and_original_durations_unchanged(self):
        x=ti(TRAINING);view=g._source_review_prompt_view(x,x.unicode)
        self.assertIn('twenty years preceding the one year test period',view)
        self.assertEqual(x.unicode,TRAINING)
        g._check_weather_frequency_roles(x,'采用测试期之前长达二十年的训练期；测试期为一年。')
        g._check_weather_frequency_roles(x,'训练期不覆盖为期一年的测试期。')
        with self.assertRaises(g.PlaceholderIntegrityError):g._check_weather_frequency_roles(x,'二十年的训练期，以覆盖为期一年的测试期。')
        self.assertNotIn('training-before-test',g._wind_source_roles(ti(TRAINING.replace('before to','after'))))
        g._check_weather_frequency_roles(ti(TRAINING+' Another process covers the test period.'),'训练先于测试；另一个过程覆盖测试期。')

    def test_future_ordinal_requires_the_original_native_atom(self):
        x=ti('Use winds of the {v1}day ahead, XYZ48.',formulas=['48th'])
        atom=x.placeholders[0].formula;state=deepcopy(atom)
        self.assertIn('future-day',g._wind_source_roles(x))
        g._check_weather_frequency_roles(x,'使用未来第{v1}天的风，XYZ48。')
        with self.assertRaises(g.PlaceholderIntegrityError):g._check_weather_frequency_roles(x,'使用{v1}天前的风，XYZ48。')
        self.assertIs(x.placeholders[0].formula,atom);self.assertEqual(atom,state)
        for word in ('x48','48','48th+1'):
            self.assertNotIn('future-day',g._wind_source_roles(ti(x.unicode,formulas=[word])))
        atom.pdf_character[0].pdf_character_id=None
        self.assertNotIn('future-day',g._wind_source_roles(x))

    def test_existing_persistence_and_averaging_bias_roles_are_separate(self):
        x=ti(BIAS);view=g._source_review_prompt_view(x,x.unicode)
        self.assertIn('wind persistence of classes A and C is strong',view);self.assertIn('considering daily average winds can make those classes quite biased',view)
        g._check_weather_frequency_roles(x,'A和C类具有较强的风持续性，考虑日平均风可能使这两类产生较大偏差。')
        with self.assertRaises(g.PlaceholderIntegrityError):g._check_weather_frequency_roles(x,'A和C类因考虑日平均风况而可能具有较强风持续性，从而产生较大偏差。')
        self.assertNotIn('persistence-bias',g._wind_source_roles(ti(BIAS.replace('can be quite biased','are not biased'))))

    def test_caption_output_identifier_is_distinct_from_input(self):
        x=ti(CAPTION,'table_caption');view=g._source_review_prompt_view(x,x.unicode)
        self.assertIn('Prediction of wind (XYZ6) from wind data of the previous(5XYZ) five days',view)
        self.assertEqual(g._prose_scientific_identifiers(x,x.unicode),g._prose_scientific_identifiers(x,view))
        g._check_weather_frequency_roles(x,'表9：从前五天（5XYZ）的风数据预测风（XYZ6）。')
        with self.assertRaises(g.PlaceholderIntegrityError):g._check_weather_frequency_roles(x,'表9：利用前五天（5XYZ）的风数据（XYZ6）预测风。')

    def test_direction_model_and_average_vector_do_not_acquire_speed(self):
        for source,bad,good in ((FORECAST,'传统风速预报模型。','传统风预报模型。'),(VECTOR,'零日平均风速。','日平均风矢量为零。')):
            x=ti(source);g._check_weather_frequency_roles(x,good)
            with self.assertRaises(g.PlaceholderIntegrityError):g._check_weather_frequency_roles(x,bad)
            with self.assertRaises(g.PlaceholderIntegrityError):g._check_weather_frequency_roles(x,bad.replace('风速','风\n速'))
            # Explicit magnitude comparison makes the broad target terminology ambiguous.
            g._check_weather_frequency_roles(ti(source+' The speed is also discussed.'),bad)
        g._check_weather_frequency_roles(ti('The wind forecast model predicts tomorrow.'),'风速预报模型。')

    def test_previous_day_comparison_keeps_its_count_scope(self):
        x=ti(WINDOW);view=g._source_review_prompt_view(x,x.unicode)
        self.assertIn('any day within the previous five days',view)
        for good in ('142天的风况与前五天中任意一天均不同。','142天的风况与此前的5天内任何一天都不同。'):
            g._check_weather_frequency_roles(x,good)
        with self.assertRaises(g.PlaceholderIntegrityError):g._check_weather_frequency_roles(x,'另有142天的风况与之前任意一天均不同。')
        self.assertNotIn('previous-day-window',g._wind_source_roles(ti(WINDOW.replace('any one of the five previous days','the previous day'))))

    def test_quotes_code_url_and_negated_examples_never_prove_roles(self):
        for source in (DAILY,OBJECTIVE,TRAINING,BIAS,CAPTION,FORECAST,VECTOR,WINDOW):
            for wrapped in ('"'+source+'"',"'"+source+"'",'`'+source+'`','<code>'+source+'</code>'):
                x=ti(wrapped);self.assertFalse(g._wind_source_roles(x));self.assertFalse(g._source_daily_observation_times(x));self.assertFalse(g._source_objective_weather_classifier(x))
            for label in ('code','reference','formula'):
                x=ti(source,label);self.assertFalse(g._wind_source_roles(x));self.assertFalse(g._source_daily_observation_times(x))
        x=ti('https://example.com/taken_twice_daily_at_00GMT_and_at_12GMT')
        self.assertFalse(g._source_daily_observation_times(x))

    def test_source_only_prompt_hooks_work_for_initial_retry_and_whole(self):
        x=ti(DAILY);il=translator(['每天00GMT和12GMT共观测两次。']*2)
        hint=g._weather_frequency_role_hint(x)
        self.assertIn(hint,g._source_review_hint(x))
        with patch.dict(g._originals,single_prompt_context=lambda self,text,**kw:text):
            self.assertIn(hint,g._guard_single_prompt_context(il,x.unicode,translate_input=x))
        token=g._review_batch.set([x])
        try:
            import json
            with patch.dict(g._originals,batch_prompt_context=lambda self,text:text):
                self.assertIn(hint,g._guard_batch_prompt_context(il,json.dumps([{'id':'x','input':x.unicode}])))
        finally:g._review_batch.reset(token)
        g._retry_translation(il,x,ParagraphTranslateTracker(),0)
        g._whole_paragraph_translation(il,x,ParagraphTranslateTracker())
        self.assertTrue(all(hint in p for p,k in il.calls));self.assertEqual(x.unicode,DAILY)
        # Regression for the untested draft's accidental hint hook in this gate.
        g._check_measured_list_prefix(x,'任何完整译文。')

    def test_supervision_clause_binds_native_period_and_future_day_without_decoding(self):
        text="We confirmed this by supervising the 5XYZ’s wind sequences of{v1}with the non correlated winds of the {v2}day ahead, XYZ48."
        x=ti(text,formulas=['2001_02','48th']);objects=[h.formula for h in x.placeholders];states=deepcopy(objects)
        self.assertIn('supervision-pair',g._wind_source_roles(x));view=g._source_review_prompt_view(x,text)
        self.assertIn('wind sequences from period {v1} as inputs',view)
        self.assertIn('winds on future day {v2}, XYZ48, as supervision',view)
        self.assertEqual(x.unicode,text);self.assertEqual(g._CANONICAL.findall(view),g._CANONICAL.findall(text))
        self.assertTrue(all(a is b for a,b in zip(objects,[h.formula for h in x.placeholders])))
        self.assertEqual(objects,states)
        g._check_weather_frequency_roles(x,'用{v1}期间的风序列作为输入，用未来第{v2}天的风作为监督。')
        with self.assertRaises(g.PlaceholderIntegrityError):g._check_weather_frequency_roles(x,'用{v1}天的风作为输入，用未来第{v2}天的风作为监督。')
        for first,last in [('x_02','48th'),('2001_02','x48'),('2001_02','48')]:
            self.assertNotIn('supervision-pair',g._wind_source_roles(ti(text,formulas=[first,last])))
        self.assertNotIn('supervision-pair',g._wind_source_roles(ti('If '+text,formulas=['2001_02','48th'])))

    def test_complete_short_footer_keeps_native_metadata_only(self):
        x=ti('February{v1}Issue {v2}e210','abandon',formulas=['2007 |','2 |'])
        self.assertTrue(g._preserve_publication_metadata(x,'abandon'))
        self.assertFalse(g._preserve_publication_metadata(x,'plain_text'))
        for text in ('February 2007 | Issue 2 | e210 extra explanation.',
                     'An example is February 2007 | Issue 2 | e210',
                     '"February 2007 | Issue 2 | e210"','February 2007 | Issue | e210'):
            self.assertFalse(g._preserve_publication_metadata(ti(text,'abandon'),'abandon'),text)
