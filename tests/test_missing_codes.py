"""Declared missing codes must survive counting, provenance and independent export."""
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.table_calculation import CalculationError, calculate, profile_columns, question_guard, validate_columns
from src.tabular import TabularService, TableError
from tests.test_tabular import StubPlanner


class MissingCodeTests(unittest.TestCase):
    def setUp(self):
        self.rows = [['01','A','10','89'], ['02','A','89','0'], ['03','99','100','-2']]
        self.profiles = profile_columns(['ID','Industry','Employees','Other'], self.rows)
        self.fields = [{'id':f'c{i}', 'label':name, 'role':role, 'unit':'', 'decimal_separator':'.',
                        'missing_values': ['99'] if i == 1 else ['89'] if i == 2 else []}
                       for i,(name,role) in enumerate(zip(['ID','Industry','Employees','Other'],
                                                        ['identifier','category','number','number']))]
        self.plan = {'status':'ready','reason':'test','metrics':['c2','c3'],'aggregation':'mean','group_by':'c1'}

    def test_codes_are_column_specific_and_grouping_is_counted(self):
        result = calculate(self.rows, validate_columns(self.profiles,self.fields), self.plan)
        self.assertEqual(result['table'][0]['values'], {'c2':10,'c3':44.5})
        self.assertEqual(result['table'][0]['missing_counts'], {'c2':1,'c3':0})
        self.assertEqual(result['quality']['grouping_missing_rows'],1)
        self.assertIn('89', ' '.join(result['limitations']))
        self.assertIn('99', ' '.join(result['limitations']))

    def test_matching_is_explicit_text_not_automatic_numeric_guessing(self):
        rows = [['01','A',' 89 ','0'],['02','A','89.0','-2']]
        result = calculate(rows,validate_columns(self.profiles,self.fields),self.plan)
        self.assertEqual(result['table'][0]['values'], {'c2':89,'c3':-1})
        self.assertEqual(result['table'][0]['missing_counts']['c2'],1)

    def test_invalid_missing_code_configuration_is_rejected(self):
        for value in ('89',[89],[''],['a'*81],['x']*21,None):
            fields = [dict(field) for field in self.fields]
            fields[2]['missing_values'] = value
            with self.subTest(value=value), self.assertRaises(CalculationError):
                validate_columns(self.profiles,fields)

    def test_declared_codes_can_leave_group_without_valid_values(self):
        result = calculate([['01','A','89','0']],validate_columns(self.profiles,self.fields),dict(self.plan,aggregation='sum'))
        self.assertIsNone(result['table'][0]['values']['c2'])
        self.assertEqual(result['table'][0]['values']['c3'],0)

    def test_unspecified_operation_requires_clarification_even_when_model_guesses(self):
        columns=validate_columns(self.profiles,self.fields)
        dataset={'columns':columns,'period':'2025'}
        plan=dict(self.plan,metrics=['c2'])
        for question in ('Сравни Employees по Industry.', 'Compare Employees by Industry.'):
            checked=question_guard(question,plan,dataset)
            self.assertEqual(checked['status'],'clarify')
            self.assertEqual(checked['metrics'],[])
        self.assertEqual(question_guard('Среднее Employees по Industry',plan,dataset)['status'],'ready')

    def make_dataset(self, service, synthetic=True):
        raw = 'ID;Industry;Employees;Other\n01;A;10;89\n02;A;89;0\n03;99;100;-2\n'.encode()
        imported = service.inspect({'filename':'coded.csv','content_base64':base64.b64encode(raw).decode()})
        return service.register({'import_id':imported['id'],'table_id':'csv:0','header_row':1,
                                 'label':'Coded responses','observation_unit':'establishment','period':'2025',
                                 'columns':self.fields,'declared_synthetic':synthetic})

    def test_service_export_reproduces_missing_rules_and_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); planner=StubPlanner(); service=TabularService(root,lambda:planner)
            dataset=self.make_dataset(service)
            self.assertEqual(dataset['columns'][2]['missing_count'],1)
            self.assertEqual(dataset['columns'][2]['invalid_count'],0)
            self.assertTrue(service.state()['datasets'][0]['synthetic_data'])
            plan=service.plan({'dataset_id':dataset['id'],'question':'Average Employees by Industry'})
            report=service.execute({'plan_id':plan['id']})
            self.assertEqual(report['table'][0]['values']['c2'],10)
            self.assertNotIn('из примеров приложения',report['limitations'][0])
            script,_,name=service.export(report['id'],'python')
            source,_,_=service.export(report['id'],'source')
            (root/name).write_bytes(script); (root/'source.csv').write_bytes(source)
            process=subprocess.run([sys.executable,'-I','-X','utf8',str(root/name),str(root/'source.csv')],
                                   capture_output=True,text=True,encoding='utf-8',timeout=30)
            self.assertEqual(process.returncode,0,process.stderr)
            actual=json.loads(process.stdout)
            self.assertEqual(actual['table'],report['table'])
            self.assertEqual(actual['quality'],report['quality'])
            self.assertEqual(actual['limitations'],report['limitations'][1:])

    def test_missing_rules_and_origin_are_bound_to_saved_plan(self):
        for mutation in ('origin','codes'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                service=TabularService(tmp,lambda:StubPlanner()); dataset=self.make_dataset(service)
                plan=service.plan({'dataset_id':dataset['id'],'question':'Average Employees by Industry'})
                if mutation == 'origin': dataset['declared_synthetic']=False
                else: dataset['columns'][2]['missing_values']=[]
                service._write(Path(tmp)/'datasets'/(dataset['id']+'.json'),dataset)
                with self.assertRaises(TableError):service.execute({'plan_id':plan['id']})

    def test_legacy_dataset_without_new_optional_metadata_remains_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=TabularService(tmp,lambda:StubPlanner()); dataset=self.make_dataset(service,False)
            dataset.pop('declared_synthetic')
            for column in dataset['columns']:column.pop('missing_values')
            dataset['fingerprint']=service._fingerprint(dataset)
            service._write(Path(tmp)/'datasets'/(dataset['id']+'.json'),dataset)
            plan=service.plan({'dataset_id':dataset['id'],'question':'Average Employees by Industry'})
            report=service.execute({'plan_id':plan['id']})
            self.assertEqual(report['table'][0]['values']['c2'],49.5)


if __name__ == '__main__':unittest.main()
