"""Independent portable regressions for exact source weather-context boundaries."""
import unittest
from test_document_weather_context import guard,fixture,ti_for,DEFINITION

class WeatherReviewBoundaryTests(unittest.TestCase):
    def bind(self,text,definition=DEFINITION):
        doc,_,para=fixture(text,definition)
        guard._capture_source_weather_terms(doc)
        ti=ti_for(para);guard._prepare_source_weather_terms(para,ti)
        return ti

    def test_single_quoted_definition_does_not_bind_but_possessive_prose_does(self):
        for literal in ["'Circulation Weather Type (CWT)'",'‘Circulation Weather Type (CWT)’',
                        '"Circulation Weather Type (CWT)"']:
            with self.subTest(literal=literal):
                ti=self.bind('The CWT analysis is applied to signals.','The phrase '+literal+' is only a quotation.')
                self.assertIsNone(guard._source_weather_context(ti))
        ti=self.bind('The CWT of earlier days is analyzed.',"WMO's labels use the source model's Circulation Weather Type (CWT).")
        self.assertIsNotNone(guard._source_weather_context(ti))

    def test_opposite_codes_at_same_location_do_not_cross_match_valid_alternatives(self):
        for source,target in [
            ('Consider an A centred on Q or a C centred on Q.','考虑以Q为中心的反气旋，或以Q为中心的气旋。'),
            ('Consider an A centred to the SE of Q or a C centred to the SE of Q.','考虑以Q东南方为中心的反气旋，或以Q东南方为中心的气旋。')]:
            ti=self.bind(source)
            self.assertEqual(len(guard._source_weather_context(ti).centres),2)
            guard._check_source_weather_terms(ti,target)
            for wrong in ['考虑气旋(A)与气旋(C)。','考虑反气旋(A)与反气旋(C)。']:
                with self.assertRaises(guard.PlaceholderIntegrityError):guard._check_source_weather_terms(ti,wrong)

    def test_unique_location_still_rejects_wrong_role(self):
        ti=self.bind('Consider an A centred to the SE of Q or a C centred to the NW of Q.')
        guard._check_source_weather_terms(ti,'考虑以Q东南方为中心的反气旋，或以Q西北方为中心的气旋。')
        with self.assertRaises(guard.PlaceholderIntegrityError):
            guard._check_source_weather_terms(ti,'考虑以Q东南方为中心的气旋，或以Q西北方为中心的气旋。')

if __name__=='__main__':unittest.main()
