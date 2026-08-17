-- O SQL anterior (sql/027) deu "ERROR: 42P01: relation 'rotina_1076_itens' does not exist" — ou seja, a
-- tabela usada pelas abas Interestadual/Interno do ICMS ST não existe neste banco (por algum motivo ela
-- nunca foi criada aqui, ou foi criada num projeto Supabase diferente do que o app está apontando agora).
--
-- Este script substitui o sql/027 e é auto-suficiente: recria a tabela do zero SE ela não existir (já com
-- a estrutura final, incluindo as colunas do layout resumido do sql/016 e a alíquota já com a precisão mais
-- larga do sql/027), e também corrige o de sefaz_st_lancamentos e os índices/RLS, caso algum desses também
-- esteja faltando. Se a tabela JÁ existir (com a estrutura antiga), os comandos "if not exists"/"add column
-- if not exists" não fazem nada de mal — e o "alter column type" no fim garante a alíquota widened de
-- qualquer forma.
--
-- IMPORTANTE: se este script criar a tabela do zero (ela realmente não existia), ela nasce VAZIA — não tem
-- como recuperar dados de uma tabela que nunca existiu. Se você já tinha importado a Rotina 1076 antes e via
-- dados na aba Interestadual/Interno, isso indica que o app estava conectado a OUTRO projeto/banco Supabase
-- naquela ocasião — vale conferir se o "DATABASE_URL" configurado no Streamlit Cloud aponta pro mesmo
-- projeto Supabase onde você está rodando este SQL Editor agora (Project Settings → Database → Connection
-- string, comparar o host/projeto).

create table if not exists rotina_1076_itens (
    id                 bigserial primary key,
    competencia_id     bigint not null references competencias(id) on delete cascade,
    dt_entrada         date,
    dt_emissao         date,
    dt_selo            date,
    num_seq_ent        text,
    nf_numero          text not null,
    produto_codigo     text,
    produto_descricao  text,
    ncm                text,
    uf                 text,
    valor_produto      numeric(14,2),
    icms_proprio       numeric(14,2),
    base_st            numeric(14,2),
    col13              numeric(14,2),
    aliq_st            numeric(9,4),
    aliq_cheia         text,
    base_st_final      numeric(14,2),
    valor_icms_st      numeric(14,2),
    formato_origem     text,
    fornecedor_codigo  text,
    fornecedor_nome    text,
    fornecedor_cnpj    text,
    importado_em       timestamptz not null default now()
);

-- caso a tabela já existisse mas com a estrutura antiga (sem as colunas do sql/016, ou com aliq_st ainda
-- em numeric(6,3)) — não faz nada se já estiver tudo certo.
alter table rotina_1076_itens add column if not exists formato_origem text;
alter table rotina_1076_itens add column if not exists fornecedor_codigo text;
alter table rotina_1076_itens add column if not exists fornecedor_nome text;
alter table rotina_1076_itens add column if not exists fornecedor_cnpj text;
alter table rotina_1076_itens alter column aliq_st type numeric(9,4);

create index if not exists ix_1076_competencia on rotina_1076_itens(competencia_id);
create index if not exists ix_1076_nf on rotina_1076_itens(nf_numero);

alter table rotina_1076_itens enable row level security;
drop policy if exists "authenticated_full_access" on rotina_1076_itens;
create policy "authenticated_full_access" on rotina_1076_itens
    for all to authenticated using (true) with check (true);

-- mesma checagem pra tabela irmã (usada pela mesma tela, aba Interestadual) — caso também esteja faltando.
create table if not exists sefaz_st_lancamentos (
    id                  bigserial primary key,
    competencia_id      bigint not null references competencias(id) on delete cascade,
    cte                 text,
    emitente            text,
    nf_numero           text not null,
    data_inclusao       date,
    data_fato_gerador   date,
    valor_total_nota    numeric(14,2),
    destinatario        text,
    credenciamento      text,
    data_vencimento     date,
    receita             text,
    calculado           numeric(14,2),
    pago                numeric(14,2),
    dae                 numeric(14,2),
    retencao            numeric(14,2),
    gnre                numeric(14,2),
    ressarcimento       numeric(14,2),
    credito_presumido   numeric(14,2),
    parcelado           numeric(14,2),
    auto_infracao       numeric(14,2),
    n_dae               text,
    situacao            text,
    importado_em        timestamptz not null default now()
);
create index if not exists ix_sefaz_st_competencia on sefaz_st_lancamentos(competencia_id);
create index if not exists ix_sefaz_st_nf on sefaz_st_lancamentos(nf_numero);

alter table sefaz_st_lancamentos enable row level security;
drop policy if exists "authenticated_full_access" on sefaz_st_lancamentos;
create policy "authenticated_full_access" on sefaz_st_lancamentos
    for all to authenticated using (true) with check (true);
