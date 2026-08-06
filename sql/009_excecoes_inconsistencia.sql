-- "Aprendizado" de inconsistências — pedido do usuário em 06/08/2026: quando o analista revisa uma
-- inconsistência, dá uma justificativa e marca "replicar nas apurações futuras", essa decisão deve valer
-- sozinha nos meses seguintes (não ficar pedindo revisão do mesmo caso de novo todo mês).
--
-- Também cobre "se eu tenho 50 erros com a mesma inconsistência, ao ajustar um deve ser possível ajustar
-- todos": como as inconsistências já são AGRUPADAS (sql/008 — uma linha por NCM, ou por parceiro+CFOP,
-- representando N itens de NF), revisar/ignorar/justificar o grupo já resolve os N itens de uma vez — não
-- precisa de nada além do agrupamento pra isso.

create table if not exists excecoes_inconsistencia (
    id                bigserial primary key,
    empresa_id        bigint not null references empresas(id) on delete cascade,
    tipo              text not null check (tipo in (
                          'ncm_st_inconsistente', 'transferencia_nao_vinculada',
                          'ncm_tributado_como_st', 'ncm_tributado_novo'
                      )),
    chave_agrupamento text not null,
    ncm               text,
    cfop              integer references cfop(codigo),
    justificativa     text not null,
    ativa             boolean not null default true,
    criado_por        uuid references auth.users(id),
    criado_por_email  text,
    created_at        timestamptz not null default now(),
    unique (empresa_id, tipo, chave_agrupamento)
);
create index if not exists ix_excecoes_empresa on excecoes_inconsistencia(empresa_id, tipo);
comment on table excecoes_inconsistencia is
    'Regras "aprendidas": quando o analista marca uma inconsistência agrupada como revisada com '
    'justificativa e pede pra replicar, entra aqui. Nas próximas apurações, as funções gerar_inconsistencias_*'
    ' checam esta tabela ANTES de sinalizar — se a combinação (empresa, tipo, chave_agrupamento) tiver uma '
    'exceção ativa, a inconsistência ainda é registrada (pra auditoria/histórico) mas já nasce com '
    'status=''revisado'' e a justificativa preenchida, sem aparecer como pendente pro analista de novo.';

alter table inconsistencias
    add column if not exists justificativa text,
    add column if not exists aplicada_por_excecao boolean not null default false;
comment on column inconsistencias.justificativa is
    'Texto livre do analista explicando por que esta inconsistência (grupo) não é um erro de verdade, ou '
    'o que foi feito para corrigi-la. Preenchido manualmente ao revisar, ou automaticamente quando '
    'aplicada_por_excecao=true (copiado de excecoes_inconsistencia.justificativa).';
comment on column inconsistencias.aplicada_por_excecao is
    'true quando esta inconsistência já nasceu resolvida porque bateu com uma regra em '
    'excecoes_inconsistencia cadastrada em competência anterior — não foi revisada manualmente desta vez.';

alter table excecoes_inconsistencia enable row level security;
drop policy if exists "authenticated_full_access" on excecoes_inconsistencia;
create policy "authenticated_full_access" on excecoes_inconsistencia
    for all to authenticated using (true) with check (true);
