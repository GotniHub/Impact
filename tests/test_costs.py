import math
import unittest
from impact import costs


class CostEstimationTests(unittest.TestCase):
    def test_full_four_thousand_estimate_includes_web_and_both_requests(self):
        result=costs.estimate(4000,costs.model_rates('gpt-5.5'))
        self.assertEqual(result['courte']['total'],680)
        self.assertEqual(result['centrale'],{'text':1000,'web':200,'total':1200})
        self.assertEqual(result['approfondie']['total'],2400)
        mini=costs.estimate(4000,costs.model_rates('gpt-5.4-mini'))
        self.assertEqual(mini['centrale']['total'],350)

    def test_unknown_models_never_inherit_a_cheaper_price(self):
        for model in ['private-model','gpt-5.5-pro','gpt-5.4-mini-preview','gpt-5.5-custom']:
            with self.subTest(model=model): self.assertIsNone(costs.model_rates(model))
        self.assertEqual(costs.model_rates('gpt-5.5-2026-04-23'),costs.model_rates('gpt-5.5'))
        self.assertEqual(costs.model_rates('gpt-5.4-mini')['input_per_million'],0.75)

    def test_zero_counts_manual_prices_and_invalid_inputs(self):
        rates={'input_per_million':1,'output_per_million':2,'search_per_thousand':3}
        self.assertEqual(costs.estimate(10,rates)['centrale'],{'text':0.3,'web':0.15,'total':0.45})
        self.assertTrue(all(s['total']==0 for s in costs.estimate(0,rates).values()))
        for count in [-1,1.5,True]:
            with self.subTest(count=count),self.assertRaises(ValueError): costs.estimate(count,rates)
        for bad in [-1,math.inf,math.nan,None,True]:
            with self.subTest(price=bad),self.assertRaises(ValueError):
                costs.estimate(10,{**rates,'input_per_million':bad})

    def test_estimate_candidate_scope_matches_launch_options(self):
        from impact import services
        rows=[{'id':'pending','assessment':{'exclusion':{'state':'clear'}}},
              {'id':'done','research_status':'completed','assessment':{'exclusion':{'state':'clear'}}},
              {'id':'retry','research_status':'error','assessment':{'exclusion':{'state':'clear'}}},
              {'id':'client','assessment':{'exclusion':{'state':'excluded'}}},
              {'id':'fuzzy','assessment':{'exclusion':{'state':'review'}}}]
        self.assertEqual([c['id'] for c in services.research_candidates(rows)],['pending','retry'])
        self.assertEqual([c['id'] for c in services.research_candidates(rows,True)],['pending','done','retry'])


if __name__=='__main__': unittest.main(verbosity=2)
