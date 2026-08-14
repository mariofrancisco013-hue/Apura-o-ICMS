-- Aba Interestadual (ICMS Substituição) — pedido do usuário em 14/08/2026: "Um campo para colocar o numero
-- do DAE que esta aquela nota" (DAE = Documento de Arrecadação Estadual, usado pra pagar a diferença/
-- pendência de uma NF). Mesmo padrão incremental de sql/019 (nao_entra_calculo): mais uma coluna na mesma
-- tabela icms_st_justificativas em vez de uma tabela nova, já que é mais um dado por (competência, NF).

alter table icms_st_justificativas
    add column if not exists numero_dae text;
