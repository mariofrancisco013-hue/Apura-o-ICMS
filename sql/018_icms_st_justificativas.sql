-- Justificativa de divergências da aba Interestadual (ICMS Substituição) — pedido do usuário em
-- 11/08/2026: "inclua uma coluna justificativa, para as divergências onde o analista deve informar do que
-- se trata a divergência: Tributação corrigida no Sistema, Solicitação de correção Sefaz, Sefaz errou no
-- calculo (A Menor), Sistema não calculou, Outra Competência. Além disso, ao lado deve ter um campo
-- observação que permita a digitação de texto livre." + renomeou "Não cobrado pela SEFAZ" para "Não
-- localizado na Sefaz", com justificativa própria (Nota não selada / Outra competência).
--
-- Uma linha por (competência, NF) — upsert nesta chave (ver salvar_justificativas_interestadual em
-- app/lib/icms_st.py). As opções válidas de `justificativa` variam por status (Divergente tem 5 opções,
-- Não localizado na Sefaz tem 2) — a validação dessas opções fica só na tela (SelectboxColumn), não tem
-- CHECK aqui, pra não travar se a lista de opções mudar no futuro sem precisar de nova migração.

create table if not exists icms_st_justificativas (
    id                    bigserial primary key,
    competencia_id        bigint not null references competencias(id) on delete cascade,
    nf_numero             text not null,
    justificativa         text,
    observacao            text,
    atualizado_por_email  text,
    atualizado_em         timestamptz not null default now(),
    unique (competencia_id, nf_numero)
);
create index if not exists ix_icms_st_justificativas_competencia on icms_st_justificativas(competencia_id);

alter table icms_st_justificativas enable row level security;

drop policy if exists "authenticated_full_access" on icms_st_justificativas;
create policy "authenticated_full_access" on icms_st_justificativas
    for all to authenticated using (true) with check (true);
