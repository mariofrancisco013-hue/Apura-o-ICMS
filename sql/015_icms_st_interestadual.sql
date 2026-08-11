-- Módulo novo: ICMS Substituição Tributária Interestadual — pedido do usuário em 10/08/2026.
--
-- 1º passo (o único implementado por enquanto): comparar o que a SEFAZ está cobrando (relatório de
-- lançamentos exportado do portal da SEFAZ, ex: "dadoslancamentos.csv") contra o que já está lançado no
-- sistema via Rotina 1076 do Winthor, por nota fiscal — pra saber quais NFs a SEFAZ já está cobrando mas
-- ainda não foram lançadas no Winthor (pendentes de entrada) e quais têm valor calculado divergente do
-- que já está no sistema. Confirmado com o usuário: só a Receita '1031' do relatório da SEFAZ entra nessa
-- conferência (é a receita específica de ICMS ST Interestadual — a '1023' é outro tipo de receita, fica
-- de fora por padrão, mas continua gravada para referência/auditoria).
--
-- `competencias.modulo` já previa o valor 'icms_st' desde o schema original — usado aqui sem precisar
-- alterar o CHECK.

create table if not exists rotina_1076_itens (
    id                 bigserial primary key,
    competencia_id     bigint not null references competencias(id) on delete cascade,
    dt_entrada         date,
    dt_emissao         date,
    dt_selo            date,
    num_seq_ent        text,             -- "NUMSEQENT" no export (número sequencial do item na entrada)
    nf_numero          text not null,
    produto_codigo     text,
    produto_descricao  text,
    ncm                text,
    uf                 text,
    valor_produto      numeric(14,2),    -- coluna 10 do export — semântica ainda não 100% confirmada
    icms_proprio       numeric(14,2),    -- coluna 11 — parece ~20% de valor_produto nas amostras vistas
    base_st            numeric(14,2),    -- coluna 12
    col13              numeric(14,2),    -- coluna 13 — semântica não confirmada (quase sempre 0)
    aliq_st            numeric(6,3),     -- coluna 14 — CONFIRMADO: alíquota efetiva de ICMS ST usada
    aliq_cheia         text,             -- "ALIQCHEIA" no export (coluna 15) — texto (vem "0" ou "6,93")
    base_st_final      numeric(14,2),    -- coluna 16
    valor_icms_st      numeric(14,2),    -- coluna 17 — CONFIRMADO: somado por NF bate exato com o
                                          -- "ICM A PAGAR (SUPPLY)" da planilha manual do usuário
    importado_em       timestamptz not null default now()
);
create index if not exists ix_1076_competencia on rotina_1076_itens(competencia_id);
create index if not exists ix_1076_nf on rotina_1076_itens(nf_numero);

create table if not exists sefaz_st_lancamentos (
    id                  bigserial primary key,
    competencia_id      bigint not null references competencias(id) on delete cascade,
    cte                 text,
    emitente             text,
    nf_numero           text not null,
    data_inclusao       date,
    data_fato_gerador   date,
    valor_total_nota    numeric(14,2),
    destinatario        text,
    credenciamento      text,
    data_vencimento     date,
    receita              text,           -- '1031' = ICMS ST Interestadual (confirmado com o usuário);
                                          -- '1023' = outra receita, fica de fora da comparação por padrão
    calculado            numeric(14,2),
    pago                 numeric(14,2),
    dae                  numeric(14,2),
    retencao              numeric(14,2),
    gnre                 numeric(14,2),
    ressarcimento         numeric(14,2),
    credito_presumido    numeric(14,2),
    parcelado             numeric(14,2),
    auto_infracao         numeric(14,2),
    n_dae                text,
    situacao             text,
    importado_em         timestamptz not null default now()
);
create index if not exists ix_sefaz_st_competencia on sefaz_st_lancamentos(competencia_id);
create index if not exists ix_sefaz_st_nf on sefaz_st_lancamentos(nf_numero);

alter table rotina_1076_itens enable row level security;
alter table sefaz_st_lancamentos enable row level security;

drop policy if exists "authenticated_full_access" on rotina_1076_itens;
create policy "authenticated_full_access" on rotina_1076_itens
    for all to authenticated using (true) with check (true);

drop policy if exists "authenticated_full_access" on sefaz_st_lancamentos;
create policy "authenticated_full_access" on sefaz_st_lancamentos
    for all to authenticated using (true) with check (true);
