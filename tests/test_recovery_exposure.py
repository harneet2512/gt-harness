from gt_engine.miniswe_integration import MiniSweAdapter


def test_preparation_does_not_consume_recovery(tmp_path):
    adapter = MiniSweAdapter(task_id="recovery", state_dir=tmp_path, predicates=[])
    adapter.start_task()
    adapter.note_failure_fingerprint("failure", epoch=0)
    adapter.note_edit(["module.py"])
    assert adapter.note_failure_fingerprint("failure", epoch=1)
    assert adapter._recovery_delivered == 0
    assert adapter._failure_first_epoch["failure"] == 0


def test_refusal_and_omission_preserve_retry_until_exact_binding(tmp_path):
    adapter = MiniSweAdapter(task_id="retry", state_dir=tmp_path, predicates=[])
    adapter.start_task()
    adapter.note_failure_fingerprint("failure", epoch=0)
    adapter.note_edit(["module.py"])
    adapter.note_failure_fingerprint("failure", epoch=1)
    rendered = adapter.prepare_recovery_delivery()
    assert rendered
    adapter.discard_pending_provider_deliveries(reason="fixture_refusal")
    assert adapter._recovery_delivered == 0
    assert adapter.prepare_recovery_delivery() == rendered
    adapter.bind_provider_payload({"messages": [{"role": "user", "content": "omitted"}]})
    assert adapter._recovery_delivered == 0
    assert adapter.prepare_recovery_delivery() == rendered
    delivery = adapter.bind_provider_payload({"messages": [{"role": "user", "content": rendered}]})
    assert delivery.delivery_ids
    assert adapter._recovery_delivered == 1
    assert adapter._failure_first_epoch["failure"] == 1
    assert not adapter.pending_transient
    assert not adapter.prepare_recovery_delivery()
    assert not adapter.note_failure_fingerprint("failure", epoch=1)


def test_edit_invalidates_unexposed_recovery_without_spending_budget(tmp_path):
    adapter = MiniSweAdapter(task_id="edit", state_dir=tmp_path, predicates=[])
    adapter.start_task()
    adapter.note_failure_fingerprint("failure", epoch=0)
    adapter.note_edit(["module.py"])
    adapter.note_failure_fingerprint("failure", epoch=1)
    old = adapter.prepare_recovery_delivery()
    adapter.note_edit(["module.py"])
    assert not adapter.prepare_recovery_delivery()
    adapter.bind_provider_payload({"messages": [{"role": "user", "content": old}]})
    assert adapter._recovery_delivered == 0
    assert adapter.note_failure_fingerprint("failure", epoch=2)
    assert adapter.prepare_recovery_delivery() != old
