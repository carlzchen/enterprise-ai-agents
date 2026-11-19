from types import SimpleNamespace

import pytest

from app.kbase.query import intent, retrieve, topic
from app.kbase.ingest import clean, parsers, split


@pytest.mark.parametrize('question,codes', [
    ('R2EL、R2FL、R2E、R2F 编码器的电源电压和最大电流分别是多少？', ['R2EL', 'R2FL', 'R2E', 'R2F']),
    ('PKP243D15 和 PKP243D23 系列电机的质量分别是多少？', ['PKP243D15', 'PKP243D23']),
    ('AR46AK-T10-3 的最高转速是多少？', ['AR46AK-T10-3']),
    ('ALM+ 与 ALM- 的最大允许电流是多少？', ['ALM+', 'ALM-']),
    ('5IK100VKEST-5H□S 在 60 Hz、220 VAC 下的额定电流是多少？', ['5IK100VKEST-5H□S']),
    ('KL-SV-80-400W 与 PKP243D15 的功率有何区别？', ['KL-SV-80-400W', 'PKP243D15']),
])
def test_external_product_questions_avoid_internal_catalogue_ranking(question, codes):
    plan = intent.classify(question)
    assert plan['route'] == 'DESC'
    assert plan['models'] == []
    assert plan['external_models'] == codes
    assert plan['series'] is None


@pytest.mark.parametrize('question', [
    '额定转矩最大的伺服电机是哪款',
    '额定功率大于 400W 的型号有哪些',
    'KL-SV-80-400W 的防护等级是不是 IP65，额定电压是否为 24VDC？',
    'KL-SV-80-400W 的额定电流是 0.6A 还是 0.8A？',
])
def test_internal_product_and_aggregate_queries_keep_structured_routes(question):
    plan = intent.classify(question)
    assert plan['external_models'] == []
    assert plan['route'] in ('PARAM', 'MIXED')


def test_explicit_external_product_overrides_previous_internal_subject(monkeypatch):
    monkeypatch.setattr(topic, 'known_models', lambda: pytest.fail('external products need no internal catalogue lookup'))
    plan = intent.classify('R2EL 编码器最大电流是多少？')
    lock = topic.resolve('R2EL 编码器最大电流是多少？', plan,
                         {'models': ['KL-SV-80-400W'], 'series': 'SERVO'}, True)
    assert lock['external_models'] == ['R2EL']
    assert lock['models'] == []
    assert topic.apply(plan, lock)['route'] == 'DESC'
    assert 'R2EL' in topic.describe(lock)


def test_external_product_followup_keeps_document_route(monkeypatch):
    monkeypatch.setattr(topic, 'known_models', lambda: set())
    question = '它的最大电流是多少？'
    plan = intent.classify(question)
    lock = topic.resolve(question, plan, {'models': [], 'series': None, 'external_models': ['R2EL']}, True)
    applied = topic.apply(plan, lock)
    assert applied['route'] == 'DESC'
    assert applied['models'] == []
    assert applied['external_models'] == ['R2EL']


def test_document_identifier_is_described_without_product_identity_requirement():
    text = topic.describe({'external_models': ['HM-60335-9E']})
    assert 'HM-60335-9E' in text
    assert '文档编号' in text
    assert '完整代码' not in text


@pytest.mark.parametrize('question,expected', [
    ('HM-60335-9E 中 ALM 端子的最大电流是多少？', ['manual']),
    ('查看 HM-60335-9E.pdf 的目录', ['manual']),
    ('HM-60335-9E2 中的参数是多少？', []),
    ('AR46A 的质量是多少？', []),
])
def test_named_document_resolution_uses_visible_document_metadata(monkeypatch, question, expected):
    monkeypatch.setattr(retrieve.authz, 'as_scope', lambda scope: SimpleNamespace(sql=lambda alias: ('VISIBLE_DOCUMENTS', ['role'])))
    def query(sql, args):
        if sql.startswith('SELECT d.title'):
            assert 'VISIBLE_DOCUMENTS' in sql
            return [{'title': 'HM-60335-9E.pdf'}]
        assert 'VISIBLE_DOCUMENTS' in sql and 'active_version IS NOT NULL' in sql
        assert args == ['role']
        return [{'doc_key': 'manual', 'title': 'Oriental Motor HM-60335-9E.pdf'},
                {'doc_key': 'catalogue', 'title': 'Oriental Motor AR_Series-AC_Input.pdf'}]
    monkeypatch.setattr(retrieve.store, 'query', query)
    assert retrieve.explicit_documents(question, 'SALES') == expected


def test_document_filter_survives_relaxing_product_filter(monkeypatch):
    monkeypatch.setattr(retrieve, '_expr', lambda *args: 'VISIBLE_DOCUMENTS')
    monkeypatch.setattr(retrieve, '_only_valid', lambda hits: hits)
    expressions = []
    def route(ctx, name, payload, expr, limit):
        expressions.append(expr)
        return []
    monkeypatch.setattr(retrieve, '_traced_route', route)
    assert retrieve.recall({'rewritten': '文档参数', 'hyde': ''}, 'ADMIN', models=['AR46A'],
                           mode='bm25', doc_keys=['manual']) == []
    assert len(expressions) == 2
    assert all('VISIBLE_DOCUMENTS' in expr and 'doc_key in ["manual"]' in expr for expr in expressions)


def test_fulltext_fallback_preserves_requested_document(monkeypatch):
    monkeypatch.setattr(retrieve.authz, 'as_scope', lambda scope: SimpleNamespace(sql=lambda alias: ('VISIBLE_DOCUMENTS', [])))
    calls = []
    def query(sql, args):
        calls.append((sql, args))
        assert 'VISIBLE_DOCUMENTS' in sql and 'd.doc_key IN %s' in sql
        assert ['manual'] in args
        return []
    monkeypatch.setattr(retrieve.store, 'query', query)
    assert retrieve.fulltext('参数', 'ADMIN', models=['AR46A'], doc_keys=['manual']) == []
    assert len(calls) == 2


def test_parent_sections_preserve_evidence_beyond_old_limit():
    markdown = '# Specifications\n' + ('Complete product data. ' * 300) + '\nCURRENT-TARGET 0.46 A'
    parents, chunks = split.split_document(markdown, 'Manual')
    assert len(parents[0]['content']) > 4000
    assert 'CURRENT-TARGET 0.46 A' in parents[0]['content']
    assert any('CURRENT-TARGET 0.46 A' in chunk['content'] for chunk in chunks)


def test_cleaning_preserves_table_alignment_numeric_cells_and_headers():
    markdown = ('Repeated header\n' * 5) + '```text\n' + ('Repeated header\n' * 5) + '60\nModel        Current\nA-10         0.46\n```\n'
    result = clean.clean_text(markdown)
    assert result.count('Repeated header') == 5
    assert '\n60\n' in result
    assert 'A-10         0.46' in result


def test_bilingual_rewrite_preserves_original_identifiers(monkeypatch):
    monkeypatch.setattr(intent, '_llm_json', lambda *args: {'rewritten': 'What is the motor mass?', 'hyde': 'Motor mass: X kg.'})
    question = 'AR46A 谐波减速型的质量是多少？'
    rewritten, hyde = intent.rewrite_hyde(question, [], '', '', None, bilingual=True)
    assert rewritten.startswith(question)
    assert 'motor mass' in rewritten
    assert hyde == 'Motor mass: X kg.'


def test_native_pdf_parser_preserves_column_positions(tmp_path, monkeypatch):
    from reportlab.pdfgen.canvas import Canvas
    path = tmp_path / 'manual.pdf'
    canvas = Canvas(str(path))
    canvas.drawString(72, 740, 'MODEL-ALPHA')
    canvas.drawString(330, 740, 'CURRENT A')
    canvas.drawString(72, 720, 'MODEL-BETA')
    canvas.drawString(330, 720, '12.34A')
    canvas.drawString(72, 680, 'This manual contains complete specifications and installation information for industrial motor products.')
    canvas.save()
    monkeypatch.setattr(parsers, '_pdf_needs_mineru', lambda *args: None)
    parsed = parsers.parse_doc(path, 'pdf', tmp_path / 'work', 'DOC')
    assert parsed['parsed_by'] == 'pdfplumber-layout'
    assert '<!-- page:1 -->' in parsed['markdown']
    import re
    assert re.search(r'MODEL-BETA\s{4,}12.34A', parsed['markdown'])


def test_literal_recall_prioritizes_signal_evidence_and_preserves_document_scope(monkeypatch):
    monkeypatch.setattr(retrieve.authz, 'as_scope', lambda scope: SimpleNamespace(sql=lambda alias: ('VISIBLE_DOCUMENTS', ['role'])))
    def query(sql, args):
        if sql.startswith('SELECT d.title'):
            assert 'VISIBLE_DOCUMENTS' in sql
            return [{'title': 'HM-60335-9E.pdf'}]
        assert 'VISIBLE_DOCUMENTS' in sql and "c.state='VALID'" in sql
        assert 'd.doc_key IN %s' in sql and ['manual'] in args
        return [
            {'chunk_id': 'unrelated', 'parent_id': 'p0', 'title': 'HM-60335-9E.pdf', 'content': '【HM-60335-9E.pdf】Other parameters', 'lexical_score': 10},
            {'chunk_id': 'signal', 'parent_id': 'p1', 'title': 'HM-60335-9E.pdf', 'content': '【HM-60335-9E.pdf】IN-COM power supply 5 to 24 VDC', 'lexical_score': 3},
        ]
    monkeypatch.setattr(retrieve.store, 'query', query)
    hits = retrieve.literal_recall('HM-60335-9E 中 IN-COM 的电源电压', 'IN-COM power supply voltage', 'ADMIN', ['manual'])
    assert [hit['chunk_id'] for hit in hits] == ['signal']


def test_parent_expansion_preserves_all_matched_passages_and_permission_filter(monkeypatch):
    parent = {'parent_id': 'p1', 'content': '原始章节开头' * 800, 'title': '参数表', 'seq': 1}
    monkeypatch.setattr(retrieve.store, 'query', lambda *args: [parent])
    monkeypatch.setattr(retrieve.authz, 'as_scope', lambda scope: SimpleNamespace(sql=lambda alias: ('1=1', [])))
    hits = [
        {'parent_id': 'p1', 'chunk_id': 'c1', 'content': '页面末尾的额定电流为 0.6 A', 'score': 0.9},
        {'parent_id': 'p1', 'chunk_id': 'c2', 'content': '型号 R2EL 的额定电压为 5 VDC', 'score': 0.8},
        {'parent_id': 'hidden', 'chunk_id': 'c3', 'content': '不可见资料', 'score': 0.7},
    ]
    results = retrieve.expand_parents(hits, 'ADMIN')
    assert len(results) == 1
    context = results[0]['context']
    assert context.startswith(hits[0]['content'])
    assert hits[1]['content'] in context
    assert context.index(hits[1]['content']) < context.index(parent['content'])
    assert '不可见资料' not in context
    assert results[0]['chunk_id'] == 'c1'
    assert results[0]['parent_seq'] == 1


@pytest.mark.parametrize('use', [True, False])
def test_duplicate_passages_do_not_crowd_out_other_sections(monkeypatch, use):
    monkeypatch.setattr(retrieve.llm_providers, 'rerank_scores', lambda query, texts: [0.95, 0.94, 0.93, 0.92])
    hits = [{'chunk_id': f'c{i}', 'parent_id': parent, 'section': parent,
             'content': f'evidence {i}', 'rrf': 1 / (60 + i)}
            for i, parent in enumerate(['p1', 'p1', 'p2', 'p3'], 1)]
    kept = retrieve.rerank('参数查询', hits, top=3, use=use)
    assert {h['parent_id'] for h in kept} == {'p1', 'p2', 'p3'}
    assert len([h for h in kept if h['parent_id'] == 'p1']) == 2


def test_wider_evidence_window_accepts_minimum_sections(monkeypatch):
    monkeypatch.setattr(retrieve.llm_providers, 'rerank_scores', lambda query, texts: [0.9, 0.8, 0.7, 0.1, 0.09, 0.08])
    hits = [{'chunk_id': str(i), 'parent_id': str(i), 'section': str(i), 'content': str(i)} for i in range(6)]
    kept = retrieve.rerank('external manual', hits, top=8, cliff={'min_k': 6})
    assert len(kept) == 6


def test_external_answer_review_uses_only_retrieved_evidence(monkeypatch):
    from app.kbase.query import answer
    calls = []
    def invoke(prompt, *args, **kwargs):
        calls.append(prompt)
        return SimpleNamespace(content='draft' if len(calls) == 1 else 'corrected [1]')
    monkeypatch.setattr(answer.llm, 'invoke', invoke)
    sources = [{'index': 1, 'title': 'manual', 'context': 'pin voltage range', 'section': 'wiring', 'type': 'kb'}]
    result = answer.generate('IN-COM voltage?', sources, '', SimpleNamespace())
    assert result == 'corrected [1]'
    assert len(calls) == 2
    assert 'pin voltage range' in calls[1]
    assert 'draft' in calls[1]


def test_signal_evidence_survives_low_semantic_rank(monkeypatch):
    monkeypatch.setattr(retrieve.llm_providers, 'rerank_scores', lambda query, texts: [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.01])
    hits = [{'chunk_id': str(i), 'parent_id': str(i), 'section': str(i), 'content': str(i),
             'routes': {'literal': 1} if i == 6 else {'dense': i + 1}} for i in range(7)]
    kept = retrieve.rerank('IN-COM power supply', hits, top=6, cliff={'min_k': 6})
    assert kept[0]['chunk_id'] == '6'


def test_literal_route_keeps_restored_full_page_content():
    fused = retrieve.rrf({'dense': [{'chunk_id': 'one', 'content': 'short passage'}],
                         'literal': [{'chunk_id': 'one', 'content': 'restored full page with signal diagram'}]})
    assert fused[0]['content'] == 'restored full page with signal diagram'
    assert set(fused[0]['routes']) == {'dense', 'literal'}


def test_related_manuals_share_the_external_evidence_window(monkeypatch):
    hits = [{'chunk_id': str(i), 'parent_id': str(i), 'section': str(i), 'content': str(i),
             'doc_key': 'DC' if i < 6 else 'AC', 'title': 'AR Series DC' if i < 6 else 'AR Series AC'} for i in range(10)]
    monkeypatch.setattr(retrieve.llm_providers, 'rerank_scores', lambda query, texts: [0.99 - i * 0.005 for i in range(len(texts))])
    kept = retrieve.rerank('AR系列电源输入', hits, top=6, cliff={'min_k': 6})
    assert {h['doc_key'] for h in kept} == {'AC', 'DC'}
    assert sum(h['doc_key'] == 'DC' for h in kept) == 3


def test_column_alignment_whitespace_does_not_exhaust_context_budget():
    from app.kbase.query import answer
    table = ('Model' + ' ' * 100 + 'Value\n') * 100
    assert answer.tokens(table) < len(table) / 10
    assert answer.tokens(table) > answer.tokens(table.replace(' ' * 100, ' '))


def test_semantic_selection_preserves_only_selected_source_pages(monkeypatch):
    prompts_seen = []
    def invoke(prompt, *args, **kwargs):
        prompts_seen.append(prompt)
        return SimpleNamespace(content='{"selected":[2]}')
    monkeypatch.setattr(retrieve.llm, 'invoke', invoke)
    hits = [{'chunk_id': str(i), 'parent_id': str(i), 'title': 'manual', 'section': str(i),
             'content': 'unrelated dimensions' if i == 0 else 'included connector list'} for i in range(2)]
    result = retrieve.rerank('included connectors', hits, semantic=True)
    assert [h['chunk_id'] for h in result] == ['1']
    assert 'included connector list' in prompts_seen[0]
    assert 'ground_truth' not in prompts_seen[0]


def test_semantic_selection_rejects_invented_candidate_ids(monkeypatch):
    monkeypatch.setattr(retrieve.llm, 'invoke', lambda *a, **kw: SimpleNamespace(content='{"selected":[999]}'))
    with pytest.raises(AssertionError):
        retrieve.semantic_evidence_rank('voltage', [{'chunk_id': 'one', 'content': '24 VDC'}], 8)
