"""Captured bibliography and split-URL boundaries, with fail-closed controls.

These run the real publication/retry path using recorded public-paper text;
no model, translation cache or network is involved.
"""
import unittest

try:
    from .test_guard_review_boundaries import (
        g, ti, formula, translator, paragraph, ParagraphTranslateTracker,
    )
except ImportError:
    from test_guard_review_boundaries import (
        g, ti, formula, translator, paragraph, ParagraphTranslateTracker,
    )


REFERENCE = ('10. SchumannJ, Strothotte T, Raab A, Laser S (1996) Assessing the effect of '
             'non-photorealistic rendered images in cad. In: Proceedings of the SIGCHI '
             'Conference on Human Factors in Computing Systems; 13–18 April 1996; '
             'New York, New York, United States. CHI 96. New York: Association for '
             'Computing Machinery. pp.35–41.')
REFERENCE_TRANSLATION = ('10. Schumann J, Strothotte T, Raab A, Laser S (1996) 评估非写实渲染图像在 CAD '
                         '中的效果。见：计算系统人机因素 SIGCHI 会议论文集；1996 年 4 月 13–18 日；'
                         '美国纽约州纽约市。CHI 96。纽约：计算机协会。页码：35–41。')
SOURCE_URL = 'http://github.com/ mbostock/d3/wiki/Gallery'
JOINED_URL = SOURCE_URL.replace('/ ', '/', 1)
SOURCE = ('D3.js (or just D3 for Data-Driven Documents) is a JavaScript library that offers '
          'an easy way to create and control interactive data-based graphical forms which '
          'run inweb browsers, as shown in the gallery at ' + SOURCE_URL + '.')
TRANSLATION = ('D3.js（或简称 D3，意为 Data-Driven Documents）是一个 JavaScript 库，提供了一种轻松'
               '创建和控制在 web 浏览器中运行的交互式数据驱动图形形式的方法，如 '
               + JOINED_URL + ' 画廊所示。')
DOMAIN_URL = 'http://jfly. iam.u-tokyo.ac.jp/color/'
JOINED_DOMAIN_URL = DOMAIN_URL.replace('. ', '.', 1)
DOMAIN_REFERENCE = ('6. Okabe M, Ito K (2008).Color universal design (cud) - how to make figures '
                    'and presentations that are friendly to colorblind people. Available: '
                    + DOMAIN_URL + '. Accessed 12 August 2014.')
DOMAIN_TRANSLATION = ('6. Okabe M, Ito K (2008)。色彩通用设计 (cud) - 如何制作对色盲人群友好的图表和演示文稿。'
                      '可用：' + JOINED_DOMAIN_URL + '。访问日期：2014 年 8 月 12 日。')


class BetterFiguresBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def setUp(self):
        g._states.clear()
        g._counters.clear()

    def publish(self, source, output, responses=()):
        p = paragraph(source)
        p.layout_label = 'plain text'
        engine = translator(responses)
        g._guard_post(engine, p, ParagraphTranslateTracker(), source, output)
        return p, engine

    def assert_accepted(self, source, good):
        original = source.unicode
        p, engine = self.publish(source, good)
        self.assertEqual(p.unicode, good)
        self.assertEqual(engine.calls, [])
        self.assertEqual(source.unicode, original)

    def assert_retries(self, source, bad, good):
        p, engine = self.publish(source, bad, [good])
        self.assertEqual(p.unicode, good)
        self.assertEqual(len(engine.calls), 1)
        self.assertTrue(engine.calls[0][1]['ignore_cache'])

    def test_captured_reference_accepts_page_range_without_a_date_exception(self):
        s = ti(REFERENCE)
        self.assertEqual(g._prose_numeric_literals(s, s.unicode)['35'], 1)
        self.assertEqual(g._prose_numeric_literals(s, s.unicode),
                         g._prose_numeric_literals(s, REFERENCE_TRANSLATION))
        self.assert_accepted(s, REFERENCE_TRANSLATION)

    def test_page_endpoints_omission_change_duplication_and_new_numbers_retry(self):
        s = ti(REFERENCE)
        for pages in ('36–41', '35–42', '41', '35', '35–41，35–41', '35–41，额外27'):
            with self.subTest(pages=pages):
                self.assert_retries(s, REFERENCE_TRANSLATION.replace('35–41', pages), REFERENCE_TRANSLATION)
        for bad in ('1997 年 4 月 13–18 日', '1996 年 4 月 12–18 日', '1996 年 4 月 13 日'):
            with self.subTest(date=bad):
                self.assert_retries(s, REFERENCE_TRANSLATION.replace('1996 年 4 月 13–18 日', bad),
                                    REFERENCE_TRANSLATION)

    def test_page_range_shape_does_not_reclassify_code_urls_names_or_decimals(self):
        for text in ('`pp.35–41`', '"pp.35–41"', '<code>pp.35–41</code>',
                     'https://example.org/pp.35–41', 'file.pp.35–41',
                     'pp.35.2–41', 'pp.35–41.2', 'pp.41–35', 'pp.35–41model'):
            with self.subTest(text=text):
                self.assertEqual(g._joined_prose_number_spans(text), [])
        f = formula(1, 'pp.35–41')
        s = ti('The expression is {v1}.', [f])
        self.assert_accepted(s, '表达式为{v1}。')
        with self.assertRaises(g.PlaceholderIntegrityError):
            g.normalize_output(s, '表达式为35–41。')
        s = ti('The decimal is 3.14 and the count is 35.')
        self.assert_retries(s, '小数为3.15，计数为35。', '小数为3.14，计数为35。')

    def test_split_url_is_not_a_scientific_variable_and_preserves_raw_text(self):
        s = ti(SOURCE)
        self.assertEqual(g._prose_scientific_identifiers(s, s.unicode), {'D3': 2})
        self.assertEqual(g._prose_scientific_identifiers(s, TRANSLATION), {'D3': 2})
        self.assert_accepted(s, TRANSLATION)
        self.assert_accepted(ti(SOURCE), TRANSLATION.replace(JOINED_URL, SOURCE_URL))
        self.assert_accepted(ti(SOURCE.replace('/ mbostock', '/\nmbostock')), TRANSLATION)

    def test_split_url_cannot_change_host_path_digits_suffix_query_or_count(self):
        s = ti(SOURCE)
        for bad_url in ('', JOINED_URL.replace('github.com', 'example.com'),
                        JOINED_URL.replace('/d3/', '/d4/'), JOINED_URL.replace('/wiki/', '/wiki2/'),
                        JOINED_URL.replace('Gallery', 'gallery'), JOINED_URL + '/extra',
                        JOINED_URL + '.html', JOINED_URL + '?view=2', JOINED_URL + '#test',
                        JOINED_URL + ';extra', JOINED_URL + ':extra', JOINED_URL + ',extra',
                        JOINED_URL + ' ' + JOINED_URL):
            bad = TRANSLATION.replace(JOINED_URL, bad_url)
            with self.subTest(url=bad_url):
                with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'source-split-url-changed'):
                    g.normalize_output(s, bad)
                self.assert_retries(s, bad, TRANSLATION)

    def test_split_url_preserves_every_same_link_occurrence(self):
        s = ti('The gallery is at ' + SOURCE_URL + ' and again at ' + JOINED_URL + '.')
        good = '画廊位于 ' + JOINED_URL + '，也位于 ' + JOINED_URL + '。'
        self.assert_accepted(s, good)
        self.assert_retries(s, '画廊位于 ' + JOINED_URL + '。', good)

    def test_source_evidence_never_joins_an_ordinary_word_or_an_opaque_example(self):
        for source in ('See https://example.org/ d3 for the experiment.',
                       'See https://example.org/ d3/demo for the experiment.',
                       'The literal is `' + SOURCE_URL + '`.',
                       'The quoted link is "' + SOURCE_URL + '".',
                       'The code is <code>' + SOURCE_URL + '</code>.',
                       'The expression is {v1}.',
                       'See https://example.org/ path/d3/wiki?test=1.',
                       'See https://example.org/ path/d3/wiki;extra.',
                       'See https://example.org/ path/d3/wiki:extra.',
                       'See https://example.org/ path/d3/wiki,extra.'):
            s = ti(source, [formula(1, SOURCE_URL)] if '{v1}' in source else [])
            with self.subTest(source=source):
                self.assertEqual(g._source_split_urls(s), set())
        s = ti(SOURCE)
        s._pagebridge_layout_label = 'code'
        self.assertEqual(g._source_split_urls(s), set())

    def test_identifiers_outside_split_url_and_formula_tokens_remain_protected(self):
        s = ti(SOURCE)
        self.assert_retries(s, TRANSLATION.replace('D3.js', 'D4.js'), TRANSLATION)
        s = ti('The identifier d3 is used beside ' + SOURCE_URL + '.')
        good = '标识符d3用在 ' + JOINED_URL + ' 旁。'
        self.assert_retries(s, good.replace('标识符d3', '标识符d4'), good)
        f = formula(1, 'x35 = 41')
        s = ti('The expression {v1} is displayed at ' + SOURCE_URL + '.', [f])
        good = '表达式{v1}展示于 ' + JOINED_URL + '。'
        self.assert_accepted(s, good)
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'formula-count-or-order'):
            g.normalize_output(s, good.replace('{v1}', ''))

    def test_captured_domain_break_keeps_adjacent_chinese_date_in_numeric_analysis(self):
        s = ti(DOMAIN_REFERENCE)
        self.assertEqual(g._source_split_urls(s), {('http://jfly.', 'iam.u-tokyo.ac.jp/color/')})
        self.assertEqual(g._prose_numeric_literals(s, s.unicode), {'2008': 1, '2014': 1, '12': 1})
        self.assertEqual(g._prose_numeric_literals(s, DOMAIN_TRANSLATION), {'2008': 1, '2014': 1, '12': 1})
        self.assertIn('。访问日期：2014 年 8 月 12 日。', g._prose_text(s, DOMAIN_TRANSLATION))
        self.assert_accepted(s, DOMAIN_TRANSLATION)
        self.assert_accepted(ti(DOMAIN_REFERENCE), DOMAIN_TRANSLATION.replace(JOINED_DOMAIN_URL, DOMAIN_URL))
        self.assert_accepted(ti(DOMAIN_REFERENCE.replace('jfly. ', 'jfly.\n')), DOMAIN_TRANSLATION)

    def test_domain_break_does_not_hide_changed_dates_or_new_numeric_prose(self):
        s = ti(DOMAIN_REFERENCE)
        for bad in (DOMAIN_TRANSLATION.replace('2014', '2015'),
                    DOMAIN_TRANSLATION.replace('12 日', '13 日'),
                    DOMAIN_TRANSLATION.replace('2014 年 ', ''),
                    DOMAIN_TRANSLATION + '额外27条记录。',
                    DOMAIN_TRANSLATION + '2014年访问。'):
            with self.subTest(output=bad):
                self.assert_retries(s, bad, DOMAIN_TRANSLATION)

    def test_domain_break_requires_exact_uri_and_multiplicity(self):
        s = ti(DOMAIN_REFERENCE)
        for bad_url in ('', JOINED_DOMAIN_URL.replace('jfly.', 'jfly2.'),
                        JOINED_DOMAIN_URL.replace('u-tokyo', 'u-kyoto'),
                        JOINED_DOMAIN_URL.replace('/color/', '/color2/'),
                        JOINED_DOMAIN_URL.rstrip('/'), JOINED_DOMAIN_URL + 'extra/',
                        JOINED_DOMAIN_URL + '?view=2014', JOINED_DOMAIN_URL + '#ref',
                        JOINED_DOMAIN_URL + ';extra', JOINED_DOMAIN_URL + ' ' + JOINED_DOMAIN_URL):
            bad = DOMAIN_TRANSLATION.replace(JOINED_DOMAIN_URL, bad_url)
            with self.subTest(url=bad_url):
                with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'source-split-url-changed'):
                    g.normalize_output(s, bad)
                self.assert_retries(s, bad, DOMAIN_TRANSLATION)

    def test_domain_break_ignores_code_formulas_and_ambiguous_incomplete_links(self):
        for source in ('The code is `' + DOMAIN_URL + '`.',
                       'The quoted example is "' + DOMAIN_URL + '".',
                       'The code is <code>' + DOMAIN_URL + '</code>.',
                       'See http://example.com. another.example/path/.',
                       'See http://jfly. words about the experiment.',
                       'See http://jfly. iam.u-tokyo.ac.jp/color/file',
                       'See http://jfly. iam.u-tokyo.ac.jp/color/ more/path/.',
                       'See http://jfly. iam.u-tokyo.ac.jp/color/?query=1.',
                       'See http://www. perceptualedge.com/articles/visual_business_ intelligence/file.pdf.',
                       'The expression is {v1}.'):
            s = ti(source, [formula(1, DOMAIN_URL)] if '{v1}' in source else [])
            with self.subTest(source=source):
                self.assertEqual(g._source_split_urls(s), set())
        f = formula(1, 'x12 = 2014')
        s = ti('The expression {v1} is displayed at ' + DOMAIN_URL + '.', [f])
        good = '表达式{v1}展示于' + JOINED_DOMAIN_URL + '。'
        self.assert_accepted(s, good)
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'formula-count-or-order'):
            g.normalize_output(s, good.replace('{v1}', ''))

    def test_split_final_tld_accepts_host_only_and_tool_paths(self):
        for source_url in ('http://junkcharts.typepad. com', 'http://www.texample. net/tikz/examples/all/',
                           'https://github. com/rougier/ten-rules', 'https://source. jp/archive/paper'):
            s = ti('The record is available at ' + source_url + '. Accessed 12 August 2014.')
            joined = source_url.replace('. ', '.', 1)
            good = '该记录位于' + joined + '。访问日期：2014年8月12日。'
            with self.subTest(url=source_url):
                self.assertTrue(g._source_split_urls(s))
                self.assert_accepted(s, good)
                self.assert_retries(s, good.replace('2014年', '2015年'), good)
                with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'source-split-url-changed'):
                    g.normalize_output(s, good.replace(joined, joined + '/changed'))
        s = ti('8. Fung K (2005). Junk charts: Recycling chartjunk as junk art. '
               'Available:http://junkcharts.typepad. com. Accessed 12 August 2014.')
        self.assert_accepted(s, '8. Fung K (2005)。垃圾图表：将图表垃圾回收为垃圾艺术。获取地址：'
                             'http://junkcharts.typepad.com。访问日期：2014 年 8 月 12 日。')

    def test_generic_prose_url_boundary_keeps_chinese_sentence_numbers(self):
        # Includes path breaks that are deliberately not inferred as URI joins:
        # the fallback's punctuation boundary must still keep the date visible.
        for raw, joined in (('http://matplotlib.org/ gallery.html', 'http://matplotlib.org/gallery.html'),
                            ('http://www.fmwconcepts.com/imagema gick/index.php',
                             'http://www.fmwconcepts.com/imagemagick/index.php'),
                            ('https://example.test/path', 'https://example.test/path')):
            s = ti('The record is at ' + raw + '. Accessed 12 August 2014.')
            good = '该记录位于' + joined + '。访问日期：2014年8月12日。'
            with self.subTest(url=raw):
                self.assertIn('2014', g._prose_text(s, good))
                self.assert_accepted(s, good)
                self.assert_retries(s, good.replace('2014年', '2015年'), good)

    def test_literal_unicode_iri_and_email_do_not_swallow_following_prose(self):
        for url in ('https://例子.测试/路径，图表', 'https://example.test/中文路径', 'author@example.org'):
            s = ti('The source is at ' + url + '. The record contains 27 samples.')
            good = '来源是' + url + '。记录包含27个样本。'
            with self.subTest(url=url):
                self.assertEqual(g._prose_numeric_literals(s, good), {'27': 1})
                self.assertNotIn('图表', g._prose_text(s, good))
                self.assert_retries(s, good.replace('27个', '28个'), good)

    def test_complete_domain_sentence_or_opaque_tld_example_is_not_joined(self):
        for source in ('See http://example.com. net.', 'See http://junkcharts.typepad. Accessed 2014.',
                       'See https://example.dev. is a good site.',
                       'See https://example.dev. in this experiment.',
                       'See https://example.dev. net benefits increased.',
                       'See https://example.dev. is.',
                       'The code `http://junkcharts.typepad. com` is literal.',
                       'The example is "http://junkcharts.typepad. com".'):
            self.assertEqual(g._source_split_urls(ti(source)), set())


if __name__ == '__main__':
    unittest.main()
