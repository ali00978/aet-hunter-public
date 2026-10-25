"""Marktplaats potential shortlist regressions: entirely offline."""
import unittest
from datetime import datetime, timedelta, timezone
from marktplaats_potential import select_potential_candidates

URL='https://www.marktplaats.nl/v/auto-s/opel/m123-corsa'
class PotentialCandidateTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc)
        self.base={'url':URL,'title':'Opel Corsa 1.2 2011','year':2011,'mileage_km':150000,
                   'price_type':'FIXED','price':1200,'seller_type':'particulier',
                   'checked_at':self.now.isoformat(),'description_source':'pagina_tekst',
                   'description':'Goed onderhouden, auto rijdt normaal. '*9}
    def car(self,i,**kw):
        return {**self.base,'url':URL.replace('m123',f'm{i}'),**kw}
    def test_bounded_300_local_candidate_pool_without_affecting_default(self):
        rows=[self.car(15000+i,price=900) for i in range(320)]
        normal=select_potential_candidates(rows,[],now=self.now)
        broad=select_potential_candidates(rows,[],limit=300,now=self.now)
        capped=select_potential_candidates(rows,[],limit=10000,now=self.now)
        self.assertEqual(len(normal),30)
        self.assertEqual(len(broad),300)
        self.assertEqual(len(capped),300)
        self.assertEqual(len({r["url"] for r in broad}),300)
        self.assertEqual(broad[0]["potential_rank"],1)
        self.assertEqual(broad[-1]["potential_rank"],300)

    def test_30_unique_even_when_forty_listings_exist(self):
        rows=[self.car(1000+i,price=900+i*20) for i in range(40)]
        results=select_potential_candidates(rows,[],now=self.now)
        self.assertEqual(len(results),30)
        self.assertEqual(len({r['url'] for r in results}),30)
        self.assertEqual([r['potential_rank'] for r in results],list(range(1,31)))
    def test_airco_and_cosmetic_issues_separate_from_fixed_price(self):
        rows=[self.car(500),self.car(501,description='Airco werkt niet, verder goede auto. '*12),
              self.car(502,title='Opel Corsa 2011 lakschade')]
        categories={r['url']:r['potential_category'] for r in select_potential_candidates(rows,[],now=self.now)}
        self.assertEqual(categories[rows[0]['url']],'vaste_prijs')
        self.assertEqual(categories[rows[1]['url']],'klein_herstel')
        self.assertEqual(categories[rows[2]['url']],'klein_herstel')
    def test_hard_risks_and_above_limit_are_not_recommended(self):
        rows=[self.car(601,title='Opel Corsa motor kapot'),
              self.car(602,title='Opel Corsa financial lease'),
              self.car(603,price=4000),self.car(604,mileage_km=330001),
              self.car(605,seller_type='handelaar'),
              self.car(606,reserved=True),self.car(607,mileage_km=330000),
              self.car(608,mileage=100000,mileage_km=200000)]
        found=select_potential_candidates(rows,[],now=self.now)
        self.assertEqual([r['url'] for r in found],[rows[6]['url']])
    def test_bidding_price_is_never_mislabeled_as_fixed(self):
        rows=[self.car(701,price_type='FAST_BID',price=None,minimum_bid_eur=500),
              self.car(702,price_type='MIN_BID',price=None,minimum_bid_eur=3300)]
        got=select_potential_candidates(rows,[],now=self.now)
        self.assertEqual(len(got),1)
        self.assertEqual(got[0]['potential_category'],'prijs_opvragen')
        self.assertIsNone(got[0]['potential_asking_price_eur'])
    def test_search_cards_unverified_and_duplicate_full_page_has_priority(self):
        hint={'url':URL,'source':'marktplaats_search_card','title':'Opel Corsa 2011',
              'price_type':'FIXED','price':900,'year':2011,'mileage_km':150000,
              'discovered_at':self.now.isoformat()}
        shown=select_potential_candidates([], [hint],now=self.now)
        self.assertEqual(shown[0]['potential_category'],'zoekkaart')
        self.assertIn('onbevestigd',shown[0]['potential_display_price'])
        full={**self.base,'url':URL+'-nieuwere-titel'}
        seen=select_potential_candidates([full],[hint],now=self.now)
        self.assertEqual(len(seen),1)
        self.assertEqual(seen[0]['potential_category'],'vaste_prijs')
    def test_empty_and_stale_do_not_get_filled_with_fake_ads(self):
        self.assertEqual(select_potential_candidates([],[],now=self.now),[])
        too_old=self.car(801,checked_at=(self.now-timedelta(days=3)).isoformat())
        self.assertEqual(select_potential_candidates([too_old],[],now=self.now),[])
        self.assertEqual(select_potential_candidates([self.car(802)],[],max_price=200,now=self.now),[])
        self.assertEqual(len(select_potential_candidates([self.car(803)],[],now=self.now)),1)
    def test_mix_contains_repair_bid_search_and_fixed_if_available(self):
        normal=[self.car(2000+i,price=1200+i*7) for i in range(35)]
        defects=[self.car(2100+i,description='Airco werkt niet, auto rijdt goed. '*10) for i in range(7)]
        bids=[self.car(2200+i,price_type='MIN_BID',price=None,minimum_bid_eur=200) for i in range(7)]
        hints=[{'url':URL.replace('m123',f'm{2300+i}'),'source':'marktplaats_search_card',
                'title':'Opel Corsa 2011','price_type':'FIXED','price':1000,
                'discovered_at':self.now.isoformat()} for i in range(5)]
        output=select_potential_candidates(normal+defects+bids,hints,now=self.now)
        self.assertEqual(len(output),30)
        self.assertEqual({r['potential_category'] for r in output},
                         {'vaste_prijs','klein_herstel','prijs_opvragen','zoekkaart'})
    def test_trade_margin_overrides_fake_cheapness_and_is_not_guaranteed_profit(self):
        # Cheaper than comparable asking prices does not necessarily cover
        # transport, repairs and resale haircut. No actual marketplace calls.
        compar={'status':'indicatieve_vraagprijzen','sample_size':6,
                'median_asking_price_eur':4000,'high_price_dispersion':False}
        strong=self.car(9001,price=1200,market_comparison=compar,
                        estimated_costs_allowance_eur=450)
        weak=self.car(9002,price=2600,market_comparison=compar,
                      estimated_costs_allowance_eur=450)
        result=select_potential_candidates([weak,strong],[],now=self.now)
        self.assertEqual([x['url'] for x in result],[strong['url'],weak['url']])
        self.assertEqual(result[0]['potential_trade_tier'],'sterke_marge_na_controle')
        self.assertEqual(result[1]['potential_trade_tier'],'marge_onvoldoende')
        self.assertLess(result[1]['potential_net_margin_eur'],
                        result[1]['potential_target_margin_eur'])
        self.assertTrue(any('geen bevestigde verkoopprijs of winst' in x
                            for x in result[0]['potential_notes']))
        self.assertTrue(any('biedgrens' in x for x in result[1]['potential_notes']))

    def test_more_profit_required_on_a_2500_euro_purchase(self):
        compar={'status':'indicatieve_vraagprijzen','sample_size':5,
                'median_asking_price_eur':4300,'high_price_dispersion':False}
        car=self.car(9003,price=2500,market_comparison=compar,
                     estimated_costs_allowance_eur=450)
        result=select_potential_candidates([car],[],now=self.now)[0]
        self.assertEqual(result['potential_target_margin_eur'],750)
        self.assertEqual(result['potential_net_margin_eur'],705)
        self.assertEqual(result['potential_trade_tier'],'marge_onvoldoende')

    def test_missing_or_bad_comparisons_never_fake_profit(self):
        cases=[
            self.car(9010),
            self.car(9011,market_comparison={'status':'indicatieve_vraagprijzen',
                'sample_size':2,'median_asking_price_eur':3000}),
            self.car(9012,market_comparison={'status':'indicatieve_vraagprijzen',
                'sample_size':5,'median_asking_price_eur':3000,
                'high_price_dispersion':True}),
        ]
        for item in select_potential_candidates(cases,[],now=self.now):
            self.assertIsNone(item['potential_net_margin_eur'])
            self.assertNotEqual(item['potential_trade_tier'],'sterke_marge_na_controle')

    def test_repair_or_unchecked_card_is_not_top_tier_despite_high_margin(self):
        compar={'status':'indicatieve_vraagprijzen','sample_size':5,
                'median_asking_price_eur':4000,'high_price_dispersion':False}
        repair=self.car(9030,price=600,market_comparison=compar,
                        description='Airco werkt niet, verder goed onderhouden. '*10)
        unchecked=self.car(9031,price=700,market_comparison=compar,
                           not_rechecked_this_run=True)
        output=select_potential_candidates([repair,unchecked],[],now=self.now)
        categories={x['url']:x for x in output}
        self.assertEqual(categories[repair['url']]['potential_category'],'klein_herstel')
        self.assertNotEqual(categories[repair['url']]['potential_trade_tier'],
                            'sterke_marge_na_controle')
        self.assertNotEqual(categories[unchecked['url']]['potential_trade_tier'],
                            'sterke_marge_na_controle')

    def test_strong_repair_research_is_sorted_before_weak_fixed_price(self):
        compar={'status':'indicatieve_vraagprijzen','sample_size':5,
                'median_asking_price_eur':3500,'high_price_dispersion':False}
        good=self.car(9041,price=850,market_comparison=compar,
                      estimated_costs_allowance_eur=400)
        poor=self.car(9042,price=2700,market_comparison=compar,
                      estimated_costs_allowance_eur=400)
        result=select_potential_candidates([poor,good],[],now=self.now)
        self.assertEqual(result[0]['url'],good['url'])
        self.assertEqual([r['potential_rank'] for r in result],[1,2])

    def test_research_bid_ceiling_never_exceeds_investment_budget(self):
        from marktplaats_potential import _profit_evidence
        reference={'status':'indicatieve_vraagprijzen',
                   'sample_size':4,'median_asking_price_eur':11000,
                   'high_price_dispersion':False}
        scenario=_profit_evidence(self.car(9101,price=800),reference,800)
        self.assertIsNotNone(scenario)
        self.assertEqual(scenario['max_buy_eur'],3000,
                         'Huge comparable asking prices never lift the EUR 3000 cap')
        self.assertGreater(scenario['net_eur'],scenario['target_eur'])

    def test_credible_but_unverified_profit_ranks_above_unknown_and_loss(self):
        # All ads remain in manual research, but not in a misleading order.
        cmp={'status':'indicatieve_vraagprijzen',
             'sample_size':5,'median_asking_price_eur':3000,
             'high_price_dispersion':False}
        best=self.car(9110,price=700,market_comparison=cmp)
        unchecked=self.car(9111,price=800,market_comparison=cmp,
                           description_source='meta_samenvatting')
        small=self.car(9112,price=1900,market_comparison=cmp)
        unknown=self.car(9113,price=1200)
        loss=self.car(9114,price=2700,market_comparison=cmp)
        result=select_potential_candidates([loss,unknown,small,unchecked,best],[],
                                           now=self.now)
        self.assertEqual([x['url'] for x in result],
                         [best['url'],unchecked['url'],small['url'],
                          unknown['url'],loss['url']])
        self.assertEqual([x['potential_sort'][0] for x in result],
                         [0,1,2,3,4])
        self.assertTrue(all(x.get('potential_rank')==i+1
                            for i,x in enumerate(result)))
if __name__=='__main__':
    unittest.main()
