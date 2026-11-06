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

    def test_unknown_policy_routes_to_human(self):
        result = agent.demo_reply('有没有终身保修？', [])
        self.assertTrue(result['handoff_recommended'])
        self.assertIn('没有匹配依据', result['reply'])

    def test_refund_intent_excludes_questions_and_negation(self):
        for message in ['请不要取消订单 DEMO1002','只查询退款状态','可以申请退款吗？']:
            self.assertFalse(agent.explicit_aftersale(message))
        self.assertTrue(agent.explicit_aftersale('请申请订单 DEMO1002 退款'))

    def test_nonfinite_budget_rejected(self):
        with self.assertRaises(ValueError):
            agent.run_tool('search_products', {'budget': float('nan')})

    def test_empty_and_out_of_stock_data(self):
        with agent.data_scope({'products': [], 'orders': [], 'policies': []}):
            self.assertEqual(agent.run_tool('search_products', {}), [])
        product = {**agent.DATA['products'][0], 'stock': 0}
        with agent.data_scope({'products':[product], 'orders':[], 'policies':[]}):
            self.assertEqual(agent.run_tool('search_products', {}), [])

    @patch.dict('os.environ', {'DEEPSEEK_API_KEY':'test-placeholder'})
    def test_model_cannot_answer_from_empty_policy_result(self):
        from io import BytesIO
        first = {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'policy1','type':'function','function':{'name':'lookup_policy','arguments':'{"query":"终身保修"}'}}]}}]}
        second = {'choices':[{'message':{'role':'assistant','content':'承诺终身保修'}}]}
        with patch('urllib.request.urlopen', side_effect=[BytesIO(json.dumps(first).encode()), BytesIO(json.dumps(second).encode())]):
            result = agent.chat('终身保修政策', [])
            self.assertNotIn('承诺终身保修', result['reply'])
            self.assertTrue(result['handoff_recommended'])

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
