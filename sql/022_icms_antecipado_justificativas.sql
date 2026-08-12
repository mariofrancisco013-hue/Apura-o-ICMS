-- Aba Antecipado (Receita 1023) — pedido do usuário em 12/08/2026: "na aba que apresenta se foi
-- encontrata na 1096 informar um campo de justificativa, com a informação 'validado' ou 'Corrreção Sefaz'"
-- + "Ao lado da justificativa do antecipado, colocar observação e deixe livre para o analista digitar o
-- que achar necessario".
--
-- Tabela SEPARADA de `icms_st_justificativas` (usada pelas abas Interestadual/Interno) — uma mesma NF pode
-- ter lançamento tanto na Receita 1031 quanto na 1023 na SEFAZ ao mesmo tempo (já visto em dado real: NF
-- 20354), então gravar na mesma tabela por nf_numero colidiria entre as abas. Ver comentário em
-- app/lib/icms_st.py, seção "Justificativa da aba Antecipado".

create table if not exists icms_antecipado_justificativas (
    id                      bigserial primary key,
    competencia_id          bigint not null references competencias(id) on delete cascade,
    nf_numero               text not null,
    justificativa           text,
    observacao              text,
    atualizado_por_email    text,
    atualizado_em           timestamptz not null default now(),
    unique (competencia_id, nf_numero)
);
create index if not exists ix_icms_antecipado_justificativas_competencia
    on icms_antecipado_justificativas(competencia_id);

alter table icms_antecipado_justificativas enable row level security;

drop policy if exists "authenticated_full_access" on icms_antecipado_justificativas;
create policy "authenticated_full_access" on icms_antecipado_justificativas
    for all to authenticated using (true) with check (true);
