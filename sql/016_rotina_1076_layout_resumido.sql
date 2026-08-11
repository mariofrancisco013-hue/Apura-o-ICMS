-- Suporte ao 2º layout do export da Rotina 1076 — achado em 11/08/2026 com o arquivo real do usuário
-- "Relatorio 1076 Atacadão F3.xls": o Winthor também exporta a Rotina 1076 já RESUMIDA por Nota Fiscal
-- (17 colunas, sem detalhe de item/produto/NCM, mas com fornecedor e CNPJ do fornecedor), diferente do
-- layout item a item já suportado (18 colunas — ver sql/015_icms_st_interestadual.sql). Confirmado que é a
-- mesma fonte de dado: as 34 NFs do arquivo resumido bateram exatas, ao centavo, contra o total por NF
-- calculado agregando o arquivo item a item já importado da mesma competência.
--
-- Estas colunas novas só existem no layout resumido (ficam NULL nas linhas importadas do layout item a
-- item, que não tem fornecedor — só produto). `formato_origem` registra qual dos dois layouts gerou cada
-- linha ('item' ou 'resumido_nf'), para dar pra saber depois, sem precisar adivinhar pelas colunas nulas.

alter table rotina_1076_itens add column if not exists formato_origem text;
alter table rotina_1076_itens add column if not exists fornecedor_codigo text;
alter table rotina_1076_itens add column if not exists fornecedor_nome text;
alter table rotina_1076_itens add column if not exists fornecedor_cnpj text;

-- linhas já importadas antes desta migração são todas do layout item a item
update rotina_1076_itens set formato_origem = 'item' where formato_origem is null;
