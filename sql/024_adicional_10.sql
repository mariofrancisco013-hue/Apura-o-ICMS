-- Aba/página "Adicional 10%" — pedido do usuário em 13/08/2026: "AGORA AO LADO DA SUBSTITUIÇÃO CRIE UMA
-- COM O NOME: ADICIONAL 10% UTILIZANDO A LOGICA DA PLANILHA ANEXA" (arquivo real:
-- "ADICIONAL 10  ATACADO F3.xls"). `competencias.modulo` já previa o valor 'icms_adicional_10' desde o
-- schema original (sql/001_schema.sql) — só faltava o módulo em si.
--
-- Lógica reconstruída direto do XML da planilha real do usuário (fórmulas das abas RESUMO/NFES/FILTRO) —
-- ver app/lib/icms_adicional10.py e claude/metodologia-adicional-10.md no projeto pra detalhe. Resumo:
--   VENDAS (do mês)     = soma de VL TOTAL das NFs cujo cliente está classificado "Sim" no cadastro
--                         (aba FILTRO da planilha) — clientes não classificados NÃO contam (mesmo
--                         comportamento do IFERROR(...,"Não") da planilha original).
--   BASE DE CALCULO     = máximo(VENDAS − 10% × FATURAMENTO, 0)
--   ADICIONAL ICMS 1%   = (BASE DE CALCULO × 19,31%) × 1%
--   ADICIONAL ICMS 4%   = (BASE DE CALCULO × 80,69%) × 4%
-- FATURAMENTO é um valor mensal digitado manualmente na planilha original (sem fórmula) — vira um campo
-- editável na tela, gravado em checkpoints_referencia (fonte='manual_adicional_10'), mesmo padrão já usado
-- pela ICMS PE pros valores manuais da linha 4.1.01 (ver sql/013_icms_pe_credito_presumido.sql).

-- 1) checkpoints_referencia ganha uma quarta "fonte": 'manual_adicional_10' — usada só pra guardar o
-- Faturamento mensal digitado (linha='faturamento', valor_icms=valor digitado).
alter table checkpoints_referencia drop constraint if exists checkpoints_referencia_fonte_check;
alter table checkpoints_referencia add constraint checkpoints_referencia_fonte_check
    check (fonte in ('rotina_1024', 'rotina_1025', 'manual_pe', 'manual_adicional_10'));

-- 2) Cadastro de clientes GLOBAL (não é por competência — mesmo princípio do cadastro_fornecedores_st do
-- módulo ICMS ST): código do cliente (Winthor) -> classificação "Sim"/"Exceção" (se a NF desse cliente
-- conta ou não na base do Adicional 10%) + nome, só informativo. Alimentado pela aba "FILTRO" da planilha
-- do usuário. Upsert por cod_cliente — reimportar atualiza quem já existe e insere quem é novo, sem apagar
-- ninguém que não veio na importação atual (mesmo padrão do cadastro_fornecedores_st).
create table if not exists icms_adicional10_clientes (
    cod_cliente     bigint primary key,
    calcula         text,
    cliente_nome    text,
    atualizado_em   timestamptz not null default now()
);

alter table icms_adicional10_clientes enable row level security;
drop policy if exists "authenticated_full_access" on icms_adicional10_clientes;
create policy "authenticated_full_access" on icms_adicional10_clientes
    for all to authenticated using (true) with check (true);

-- 3) NFs importadas (aba "NFES" da planilha) — POR COMPETÊNCIA (apagar+inserir, mesmo padrão do resto do
-- projeto). A planilha do usuário tem essa aba como um acumulado de vários meses ao mesmo tempo — o import
-- da plataforma agrupa automaticamente cada linha na sua própria competência pela data de Emissão (criando
-- a competência se ainda não existir), então um único arquivo enviado pode alimentar vários meses de uma
-- vez.
create table if not exists icms_adicional10_nfes_itens (
    id                  bigserial primary key,
    competencia_id      bigint not null references competencias(id) on delete cascade,
    n_trans             text,
    nfe                 text,
    serie               text,
    tv                  text,
    filial              text,
    emissao             date,
    cnpj                text,
    rca                 text,
    cod_cliente         bigint,
    cliente_nome        text,
    uf                  text,
    ie                  text,
    vl_total            numeric(14,2),
    obs                 text,
    importado_em        timestamptz not null default now()
);
create index if not exists ix_icms_adicional10_nfes_itens_competencia
    on icms_adicional10_nfes_itens(competencia_id);

alter table icms_adicional10_nfes_itens enable row level security;
drop policy if exists "authenticated_full_access" on icms_adicional10_nfes_itens;
create policy "authenticated_full_access" on icms_adicional10_nfes_itens
    for all to authenticated using (true) with check (true);
