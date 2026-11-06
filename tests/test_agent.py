import json
import unittest
from unittest.mock import patch
import agent


class AgentTests(unittest.TestCase):
    def test_budget_filter(self):
        rows = agent.run_tool('search_products', {'query': '', 'budget': 100})
        self.assertTrue(rows)
        self.assertTrue(all(p['price'] <= 100 and 'cost' not in p for p in rows))

    def test_missing_order(self):
        self.assertIn('error', agent.run_tool('get_order', {'order_id': 'DEMO9999'}))

    def test_ranking_math(self):
        result = agent.run_tool('analyze_selection', {})['ranking']
        self.assertEqual(result[0]['name'], '轻行保温杯')
        self.assertAlmostEqual(result[0]['score'], 94.65, places=2)

    def test_five_demo_paths(self):
        for text, tool in [('怎么退货','lookup_policy'),('推荐100元以内商品','search_products'),('选品分析','analyze_selection'),('给云感降噪耳机写文案','search_products'),('查询订单DEMO1001','get_order')]:
            result = agent.demo_reply(text, [])
            self.assertEqual(result['trace'][0]['name'], tool)
            self.assertTrue(result['reply'])

    def test_followup(self):
        result = agent.demo_reply('给它写文案', [{'role':'user','content':'云感降噪耳机'}])
        self.assertIn('云感降噪耳机', result['reply'])

    @patch.dict('os.environ', {'DEEPSEEK_API_KEY':'test-placeholder'})
    def test_model_tool_roundtrip(self):
        first = {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'get_order','arguments':'{"order_id":"DEMO1001"}'}}]}}]}
        second = {'choices':[{'message':{'role':'assistant','content':'模拟订单运输中'}}]}
        from io import BytesIO
        with patch('urllib.request.urlopen', side_effect=[BytesIO(json.dumps(first).encode()), BytesIO(json.dumps(second).encode())]) as mock:
            result = agent.chat('查订单', [])
            self.assertEqual(result['reply'], '模拟订单运输中')
            payload = json.loads(mock.call_args_list[1].args[0].data)
            self.assertEqual(payload['messages'][-1]['tool_call_id'], 'call1')
            self.assertEqual(result['trace'][0]['name'], 'get_order')


if __name__ == '__main__':
    unittest.main()
