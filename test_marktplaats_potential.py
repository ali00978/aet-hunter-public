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
if __name__=='__main__':
    unittest.main()
