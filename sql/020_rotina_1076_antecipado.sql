-- Aba Antecipado (Receita 1023) — pedido do usuário em 12/08/2026: "permita uma nova importação da 1076
-- com os itens nessa aba sem interferir nas abas interestadual e interno".
--
-- Até aqui, a aba Antecipado lia da MESMA tabela rotina_1076_itens usada pelas abas Interestadual e Interno
-- (que é apagar+inserir por competência — reimportar substitui tudo). O usuário quer poder importar um
-- arquivo da Rotina 1076 específico pra conferir a Receita 1023 (normalmente um export item a item, pra
-- ter o detalhe de produtos) sem que essa reimportação apague/substitua os dados que as abas Interestadual
-- e Interno já usam. Por isso, tabela SEPARADA, mesma estrutura de rotina_1076_itens, mas isolada — nada
-- que acontece aqui afeta a tabela original, e vice-versa.

create table if not exists rotina_1076_antecipado_itens (
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
create index if not exists ix_rotina_1076_antecipado_itens_competencia
    on rotina_1076_antecipado_itens(competencia_id);
create index if not exists ix_rotina_1076_antecipado_itens_nf
    on rotina_1076_antecipado_itens(competencia_id, nf_numero);

alter table rotina_1076_antecipado_itens enable row level security;

drop policy if exists "authenticated_full_access" on rotina_1076_antecipado_itens;
create policy "authenticated_full_access" on rotina_1076_antecipado_itens
    for all to authenticated using (true) with check (true);
