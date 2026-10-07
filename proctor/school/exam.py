"""Lease-bound child exam. Public HTML never contains correct answers."""
import json,os,time
from pathlib import Path
from html import escape
from .database import SchoolDB,SchoolError


def exam_html(spec):
    sections=[]
    for i,q in enumerate(spec['questions']):
        options=''.join(f'<label><input type="radio" name="q_{q["id"]}" value="{n}"> {escape(text)}</label>' for n,text in enumerate(q['options']))
        sections.append(f'<fieldset data-id="{q["id"]}"><legend>{i+1}. {escape(q["text"])}</legend>{options}</fieldset>')
    return '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font:16px/1.5 "Segoe UI",Arial,sans-serif;color:#202a38;background:#f6f8fb;margin:0;padding:32px}main{max-width:760px;margin:auto}h1{font-size:28px}fieldset{margin:24px 0;padding:20px;border:1px solid #ccd5e2;border-radius:8px;background:white}legend{padding:0 8px;font-weight:600}label{display:block;padding:12px 8px;cursor:pointer}input{margin-right:8px}button{min-height:44px;padding:12px 24px;background:#2863a8;border:0;border-radius:8px;color:white;font-size:16px}button:focus,input:focus{outline:3px solid #68a9ef}#error{color:#ad3029}</style>
<main><h1>'''+escape(spec['title'])+'</h1><p>'+escape(spec['name'])+''' · ответы сохраняются локально.</p>'''+''.join(sections)+'''<button id="submit" onclick="submitQuiz()">Завершить тест</button><p id="error" role="alert"></p></main>
<script>
function collectAnswers(){const a={};document.querySelectorAll('fieldset[data-id]').forEach(q=>{let c=q.querySelector('input:checked');if(c)a[q.dataset.id]=Number(c.value)});return a;}
function submitQuiz(){if(!window.bridge){document.getElementById('error').textContent='Подождите загрузки страницы';return;}document.getElementById('submit').disabled=true;window.bridge.finish(JSON.stringify(collectAnswers()));}
</script></html>'''


class SchoolExam:
    def __init__(self,db_path,lease):
        self.db=SchoolDB(db_path);self.lease=lease;self.spec=self.db.claim(lease)
        self.closed=False;self.disposed=False;self.result=None

    @classmethod
    def from_environment(cls):
        lease=os.environ.pop('PROCTOR_ATTEMPT_LEASE','');path=os.environ.pop('PROCTOR_SCHOOL_DB','')
        if not lease:return None
        if not path:raise SchoolError('Не задана локальная база')
        return cls(path,lease)

    def started(self):self.db.start(self.lease)

    def submit(self,answers):
        self.result=self.db.finish(self.lease,answers);self.closed=True;return self.result

    def close(self):
        if self.disposed:return
        if not self.closed:
            with self.db.tx():
                self.db.con.execute("UPDATE attempts SET status='interrupted',ended=? WHERE lease_hash=? AND status IN ('preparing','calibrating','running')",(time.time(),self.db.digest(self.lease)))
            self.closed=True
        try:self.db.archive_evidence(self.lease)
        finally:self.db.close();self.disposed=True
