-- Cadastro de NCMs "tributados" (não-ST, geram crédito/débito pleno) por empresa — pedido do usuário em
-- 06/08/2026. Usado para sinalizar duas situações na aba "NCMs Tributados" da página ICMS Normal:
--   1. NCM cadastrado aqui que aparecer classificado como ST num item importado (pode ser erro de CFOP,
--      ou o produto deixou de ser tributado).
--   2. NCM NÃO cadastrado que aparecer como não-ST/tributado — sinalizado como "candidato novo" para o
--      analista decidir se deve entrar na lista (a lista cresce por decisão humana, não automaticamente).
-- Rode este arquivo no SQL Editor do Supabase DEPOIS de já ter rodado 001_schema.sql.

create table if not exists ncms_tributados (
    id            bigserial primary key,
    empresa_id    bigint not null references empresas(id) on delete cascade,
    ncm           text not null,
    descricao     text,
    criado_por    uuid references auth.users(id),
    created_at    timestamptz not null default now(),
    unique (empresa_id, ncm)
);
create index if not exists ix_ncms_tributados_empresa on ncms_tributados(empresa_id);

alter table ncms_tributados enable row level security;
drop policy if exists "authenticated_full_access" on ncms_tributados;
create policy "authenticated_full_access" on ncms_tributados
    for all to authenticated using (true) with check (true);

-- Dois novos tipos de inconsistência (ver app/lib/ncm_tributado.py). O nome da constraint segue o padrão
-- automático do Postgres para "check" inline sem nome (<tabela>_<coluna>_check); se o seu banco tiver dado
-- outro nome a essa constraint, ajuste o "drop constraint" abaixo antes de rodar.
alter table inconsistencias drop constraint if exists inconsistencias_tipo_check;
alter table inconsistencias add constraint inconsistencias_tipo_check
    check (tipo in (
        'ncm_st_inconsistente', 'transferencia_nao_vinculada',
        'ncm_tributado_como_st', 'ncm_tributado_novo'
    ));

-- Seed: os 22 NCMs informados pelo usuário em 06/08/2026 para a Sodine Atacado F3, cadastrados como
-- "tributados" (não-ST) — os que de fato geram crédito/débito pleno, esperados nos CFOPs 1102/1202/5102/
-- 6102/5927. "on conflict do nothing" torna seguro rodar de novo sem duplicar.
insert into ncms_tributados (empresa_id, ncm)
select (select id from empresas where cnpj = '07.342.785/0005-53'), ncm
from (values
    ('84701000'), ('82119390'), ('85171830'), ('62101000'), ('49019900'), ('84391030'), ('62129000'),
    ('62014000'), ('61069000'), ('62034200'), ('82130000'), ('84201010'), ('84729099'), ('95030097'),
    ('61019090'), ('85167990'), ('61034900'), ('39191020'), ('61143000'), ('82119400'), ('49030000'),
    ('63072000')
) as t(ncm)
on conflict (empresa_id, ncm) do nothing;
