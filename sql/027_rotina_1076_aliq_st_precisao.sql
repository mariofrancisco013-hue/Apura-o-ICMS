-- Corrige o erro "pandas.errors.DatabaseError" (mensagem redigida pelo Streamlit Cloud) ao importar a
-- Rotina 1076 Sintético — pedido do usuário em 14/08/2026: "Estou importando 1076 e esta com este erro".
--
-- Causa mais provável: `rotina_1076_itens.aliq_st` ficou definida como numeric(6,3) desde a criação da
-- tabela (sql/015), o que só aceita até 999,999 — qualquer alíquota (ou célula com formato inesperado do
-- Winthor) que passe disso estoura ("numeric field overflow") e o insert inteiro falha. Toda tabela criada
-- DEPOIS dessa (rotina_1076_antecipado_itens em sql/020, relatorio_1096_itens em sql/021,
-- credito_presumido_1076_itens em sql/023) já usa numeric(9,4) para colunas de alíquota — esta é a única
-- que ficou pra trás com a precisão mais estreita. Alinhando aqui com o mesmo padrão.
--
-- alter column ... type é seguro/não-destrutivo indo de numeric(6,3) para numeric(9,4) (amplia a faixa
-- aceita, não estreita) — não precisa de "using", conversão numeric->numeric é implícita.

alter table rotina_1076_itens alter column aliq_st type numeric(9,4);
