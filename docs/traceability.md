# Матрица трассировки

Строится командой `leasing trace` из docs/srs.md, диаграмм, openapi.yaml, scoring.wsdl и тестов.
Руками не правится: тест сверяет файл с тем, что строится из артефактов.

| Требование | Диаграммы | API | Тесты |
|---|---|---|---|
| **FR-01** Подача заявки | activity-submit, class-domain, sequence-submit, use-case | POST /api/v1/applications | 2: `test_contracts::test_responses_match_openapi`<br>`test_gateway::test_application_accepted_and_sent` |
| **FR-02** Проверка до банка | activity-submit | POST /api/v1/applications | 3: `test_gateway::test_bad_field_rejected_before_bank`<br>`test_gateway::test_idempotency_key_required`<br>`test_gateway::test_validation_messages_in_russian` |
| **FR-03** Безопасный повтор | activity-submit, sequence-submit, use-case | POST /api/v1/applications | 3: `test_gateway::test_same_key_other_data_conflicts`<br>`test_gateway::test_same_key_same_data_returns_same_application`<br>`test_units::test_store_idempotency` |
| **FR-04** Передача в банк | activity-submit, component, sequence-submit, state-application | POST /api/v1/applications, SOAP SubmitApplication | 2: `test_bank_soap::test_submit_returns_received`<br>`test_gateway::test_application_accepted_and_sent` |
| **FR-05** Банк отверг поле | activity-submit, sequence-submit, state-application | POST /api/v1/applications | 2: `test_bank_soap::test_validation_fault_names_field`<br>`test_gateway::test_bank_validation_fault_marks_invalid` |
| **FR-06** Банк недоступен | activity-submit, component, sequence-submit, state-application, use-case | POST /api/v1/applications | 4: `test_bank_soap::test_same_request_id_is_duplicate`<br>`test_gateway::test_attempts_run_out`<br>`test_gateway::test_bank_down_waits_and_retry_sends`<br>`test_gateway::test_late_answer_does_not_duplicate_in_bank` |
| **FR-07** Статус заявки | sequence-decision, use-case | GET /api/v1/applications/{id}, SOAP GetDecision | 5: `test_gateway::test_bank_down_on_status_keeps_status`<br>`test_gateway::test_decision_approved`<br>`test_gateway::test_final_status_does_not_call_bank`<br>`test_gateway::test_in_review_keeps_sent`<br>`test_gateway::test_unknown_application_404` |
| **FR-08** Решение банка | class-domain, sequence-decision, state-application | GET /api/v1/applications/{id} | 3: `test_contracts::test_responses_match_openapi`<br>`test_gateway::test_decision_approved`<br>`test_gateway::test_decision_declined_with_reason` |
| **FR-09** История статусов | class-domain, sequence-decision, state-application | — | 3: `test_docs::test_state_diagram_equals_code`<br>`test_units::test_final_statuses_have_no_exit`<br>`test_units::test_illegal_transition_rejected_and_history_untouched` |
| **FR-10** Отчёты | component, use-case | — | 4: `test_reports::test_approval_by_asset`<br>`test_reports::test_decision_time`<br>`test_reports::test_decline_reasons`<br>`test_reports::test_stuck_in_review` |
| **NFR-01** Таймаут банка | component, sequence-submit | — | 1: `test_gateway::test_late_answer_does_not_duplicate_in_bank` |
| **NFR-02** Безопасный разбор XML | component | — | 2: `test_bank_soap::test_oversized_body_rejected`<br>`test_bank_soap::test_xxe_is_not_resolved` |
| **NFR-03** Контракты не расходятся | — | — | 2: `test_contracts::test_required_fields_agree`<br>`test_contracts::test_three_contracts_agree` |
