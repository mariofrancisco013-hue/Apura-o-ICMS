-- Aba Crédito Presumido (Subvenção) — pedido do usuário em 12/08/2026: "Agora temos a aba depois da
-- planilha e antecipado, o nome é Credito Presumido, Ela é calculada através da planilha, 1076 analítico
-- somente do que entrou no mês apurado, a colina aliq ST deve ser comparada com a tabela de e para, e nos
-- que tem mais de uma alíquota no de-para conferir o estado de origem para definir a correta, apos isso
-- definir o vlr sem st, para aqueles que estiverem com 20% o vl st ret dev se repetir para os que forem
-- diferente de 20% vai ser o % decreto encontrado vezes base ST".
--
-- Lógica reconstruída e conferida célula a célula contra a planilha real do usuário ("subvenção.xlsx", aba
-- "CALCULO SUBV", fórmulas de P2/S2/T2) — ver app/lib/icms_st.py, seção "Crédito Presumido", pra detalhe.

-- 1) Import isolado do layout "1076 Analítico" (20 colunas — o mesmo que já existiu antes pra aba
-- Antecipado e foi descontinuado quando ela passou a usar o Relatório 1096). Reaproveitado aqui porque é
-- exatamente a fonte que a planilha "CALCULO SUBV" do usuário usa (conferido campo a campo: VL BASE=
-- icms_proprio_base, VL ICMS=icms_proprio, VL TOTAL=valor_produto, % RET=aliq_st, BASE ST=base_st_final,
-- VL ST RET=valor_icms_st — todos batendo exato com a NF 64185 real). Tabela SEPARADA de rotina_1076_itens
-- e de relatorio_1096_itens — reimportar aqui não afeta as outras abas.
create table if not exists credito_presumido_1076_itens (
    id                  bigserial primary key,
    competencia_id      bigint not null references competencias(id) on delete cascade,
    dt_entrada          date,
    dt_emissao          date,
    dt_selo             date,
    num_seq_ent         text,
    nf_numero           text not null,
    produto_codigo      text,
    produto_descricao   text,
    ncm                 text,
    uf                  text,
    valor_produto       numeric(14,2),
    icms_proprio        numeric(14,2),
    base_st             numeric(14,2),
    col13               numeric(14,2),
    aliq_st             numeric(9,4),
    aliq_cheia          text,
    base_st_final       numeric(14,2),
    valor_icms_st       numeric(14,2),
    formato_origem      text,
    fornecedor_codigo   text,
    fornecedor_nome     text,
    fornecedor_cnpj     text,
    importado_em        timestamptz not null default now()
);
create index if not exists ix_credito_presumido_1076_itens_competencia
    on credito_presumido_1076_itens(competencia_id);
create index if not exists ix_credito_presumido_1076_itens_nf
    on credito_presumido_1076_itens(competencia_id, nf_numero);

alter table credito_presumido_1076_itens enable row level security;
drop policy if exists "authenticated_full_access" on credito_presumido_1076_itens;
create policy "authenticated_full_access" on credito_presumido_1076_itens
    for all to authenticated using (true) with check (true);

-- 2) Tabela de-para GLOBAL (não é por competência — é uma referência fixa, extraída da aba "TABELA" da
-- planilha do usuário: % RET [alíquota reduzida usada pelo sistema, = aliq_st da 1076] -> % DECRETO
-- [alíquota "cheia"/decreto, usada pra calcular o quanto de ICMS ST seria devido sem a redução]. 77 pares
-- únicos — só um deles (% RET = 5,12) tem duas respostas possíveis, desempatadas por região de origem do
-- fornecedor (confirmado com o usuário em 12/08/2026): 7,25% pra Sul/Sudeste exceto ES, 9,42% pra Norte/
-- Nordeste/Centro-Oeste e ES. regiao_origem fica NULL nos outros 75 pares (sem ambiguidade, vale pra
-- qualquer origem).
create table if not exists icms_credito_presumido_depara (
    id              bigserial primary key,
    aliq_ret        numeric(9,4) not null,
    aliq_decreto    numeric(9,4) not null,
    regiao_origem   text
);
create index if not exists ix_icms_credito_presumido_depara_aliq_ret
    on icms_credito_presumido_depara(aliq_ret);

alter table icms_credito_presumido_depara enable row level security;
drop policy if exists "authenticated_full_access" on icms_credito_presumido_depara;
create policy "authenticated_full_access" on icms_credito_presumido_depara
    for all to authenticated using (true) with check (true);

-- Seed: apaga e reinsere pra migração poder ser rodada de novo com segurança (ex: se precisar corrigir
-- algum par no futuro).
delete from icms_credito_presumido_depara;
insert into icms_credito_presumido_depara (aliq_ret, aliq_decreto, regiao_origem) values
    (2.08, 2.82, null),
    (2.19, 2.96, null),
    (2.99, 5.08, null),
    (4.08, 7.7, null),
    (4.16, 5.5, null),
    (4.27, 7.26, null),
    (4.53, 7.7, null),
    (4.78, 8.13, null),
    (5.08, 5.82, null),
    (5.12, 7.25, 'sul_sudeste'),
    (5.12, 9.42, 'n_ne_co_es'),
    (5.19, 5.96, null),
    (5.33, 5.33, null),
    (5.99, 8.08, null),
    (6.58, 12.42, null),
    (6.88, 10.25, null),
    (6.93, 6.93, null),
    (7.08, 10.7, null),
    (7.09, 7.09, null),
    (7.27, 10.26, null),
    (7.47, 10.05, null),
    (7.53, 10.7, null),
    (7.78, 11.13, null),
    (8.31, 15.7, null),
    (8.33, 8.33, null),
    (8.34, 15.42, null),
    (8.53, 8.53, null),
    (8.59, 8.59, null),
    (8.86, 11.5, null),
    (8.9, 8.9, null),
    (9.12, 13.25, null),
    (9.49, 12.83, null),
    (9.5, 9.5, null),
    (9.82, 15.42, null),
    (10.03, 10.03, null),
    (10.14, 10.14, null),
    (10.16, 11.5, null),
    (10.58, 18.42, null),
    (10.83, 10.83, null),
    (10.84, 19.71, null),
    (10.96, 20.7, null),
    (11.12, 15.42, null),
    (11.25, 14.59, null),
    (11.59, 15.7, null),
    (11.9, 11.9, null),
    (11.93, 21, null),
    (12.17, 14.75, null),
    (12.35, 12.35, null),
    (12.7, 21.93, null),
    (12.72, 23.7, null),
    (12.74, 18.93, null),
    (12.83, 20.7, null),
    (13.01, 21.7, null),
    (13.33, 13.33, null),
    (13.47, 16.05, null),
    (13.49, 18.83, null),
    (13.69, 22.46, null),
    (14.31, 21.7, null),
    (14.43, 16.93, null),
    (14.7, 41.8, null),
    (14.96, 26.7, null),
    (15.54, 15.54, null),
    (16.29, 20.4, null),
    (16.88, 25.85, null),
    (19.84, 30.39, null),
    (20, 20, null),
    (21.54, 33, null),
    (21.58, 31.85, null),
    (22.88, 31.85, null),
    (23.3, 34.76, null),
    (24.54, 35.09, null),
    (24.68, 37.8, null),
    (25, 25, null),
    (25.54, 39, null),
    (25.84, 36.39, null),
    (26.44, 39.56, null),
    (28.68, 43.8, null);
