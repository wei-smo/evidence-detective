import unittest

from evidence_detective.models import Report
from evidence_detective.storage import markdown


class ReportStatusTests(unittest.TestCase):
    def test_incomplete_service_is_not_exported_as_a_material_shortage(self):
        report = Report('test', '张飞是谁', 'now', verdict='证据不足',
                        stop_code='service_incomplete',
                        stop_reason='支持度审查未完成。').to_dict()
        report['service_failures'] = [{'stage': 'analyze', 'resolved': False}]
        text = markdown(report)
        self.assertIn('调查状态：服务未完成', text)
        self.assertNotIn('结论：证据不足', text)

    def test_completed_evidence_shortage_keeps_its_meaning(self):
        report = Report('test', '虚构图书馆开放时间', 'now', verdict='证据不足',
                        stop_code='no_new_evidence').to_dict()
        self.assertIn('结论：证据不足', markdown(report))


if __name__ == '__main__':
    unittest.main()
