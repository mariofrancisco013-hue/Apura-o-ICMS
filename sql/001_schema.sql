-- Apuração ICMS — schema inicial (reconstrução de 05/08/2026)
-- Banco: Supabase (Postgres). Autenticação via Supabase Auth (auth.users) — todos os usuários
-- autenticados têm o mesmo nível de acesso (decisão do usuário em 05/08/2026: sem perfis por enquanto).

-- ============================================================================================
-- EMPRESAS (cadastro do grupo econômico, usado para validar transferências entre CFOPs)
-- ============================================================================================
create table if not exists empresas (
    id                bigserial primary key,
    filial_winthor    text,
    razao_social      text not null,
    cnpj              text not null unique,
    cnpj_raiz         text generated always as (
                          left(regexp_replace(cnpj, '[^0-9]', '', 'g'), 8)
                      ) stored,
    inscricao_estadual text,
    inscricao_municipal text,
    uf                text,
    regime            text,
    is_empresa_apurada boolean not null default false, -- true para a Sodine Atacado F3 (empresa deste projeto)
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);
comment on column empresas.cnpj_raiz is
    'Primeiros 8 dígitos do CNPJ. Duas empresas são consideradas vinculadas (podem transferir mercadoria '
    'sem gerar sinalização) se, e somente se, compartilham a mesma cnpj_raiz — regra confirmada com o '
    'usuário em 05/08/2026 (ver claude/empresas-grupo.md no projeto).';

-- ============================================================================================
-- CFOP (tabela de referência oficial + ajustes manuais para casos que a descrição não cobre)
-- ============================================================================================
create table if not exists cfop (
    codigo             integer primary key,
    descricao          text not null,
    is_st_padrao       boolean not null default false, -- inferido automaticamente da descrição oficial
    is_st_ajuste       boolean,                          -- null = usa is_st_padrao; true/false = override manual
    is_transferencia   boolean not null default false,
    regra_especial     text,  -- ex: '5927: estorno do crédito ocorre em outro lançamento, destaque é válido'
    observacao         text,
    updated_at         timestamptz not null default now()
);
comment on column cfop.is_st_ajuste is
    'Override manual do is_st_padrao. Existe porque a descrição do CFOP nem sempre revela sozinha se a '
    'operação é de mercadoria sujeita a ST (ex: CFOP 6202 "DEV. DE COMPRA PARA COMERCIALIZACAO" é usado '
    'por esta empresa para devolução de mercadoria ST, mas a descrição genérica não indica isso — achado '
    'de 05/08/2026, validado contra a Rotina 1025 de julho/2026).';

-- view auxiliar: is_st "efetivo" = ajuste manual se existir, senão o padrão
create or replace view cfop_efetivo as
    select codigo, descricao,
           coalesce(is_st_ajuste, is_st_padrao) as is_st,
           is_transferencia, regra_especial, observacao
    from cfop;

-- ============================================================================================
-- COMPETENCIAS (períodos de apuração, um por empresa+ano+mês)
-- ============================================================================================
create table if not exists competencias (
    id            bigserial primary key,
    empresa_id    bigint not null references empresas(id),
    ano           integer not null,
    mes           integer not null check (mes between 1 and 12),
    modulo        text not null default 'icms_normal'
                  check (modulo in ('icms_normal','icms_st','icms_antecipado','icms_adicional_10')),
    status        text not null default 'aberta'
                  check (status in ('aberta','importada','calculada','fechada')),
    created_at    timestamptz not null default now(),
    unique (empresa_id, ano, mes, modulo)
);

-- ============================================================================================
-- NOTAS FISCAIS — ITENS (dado bruto importado dos relatórios de Entrada/Saída)
-- ============================================================================================
create table if not exists notas_fiscais_itens (
    id                bigserial primary key,
    competencia_id    bigint not null references competencias(id) on delete cascade,
    tipo_operacao     text not null check (tipo_operacao in ('entrada','saida')),
    parceiro          text,          -- fornecedor (entrada) ou cliente (saída)
    nf_numero         text,
    tipo_genero_item  text,          -- código bruto da coluna "Tipo/Gênero" ou "Tipo/Item" — não usado em regra
    data_emissao      date,
    data_entrada      date,          -- só preenchido para tipo_operacao = 'entrada'
    produto           text,
    ncm               text,
    cfop              integer not null references cfop(codigo),
    valor_produto     numeric(14,2),
    aliq_fcp          numeric(6,3),
    valor_fcp         numeric(14,2),
    aliq_icms         numeric(6,3),
    base_icms         numeric(14,2),
    valor_icms        numeric(14,2),
    valor_total       numeric(14,2),
    uf                text,
    prazo_dias        integer,
    colunas_nao_identificadas jsonb, -- guarda as colunas ainda não mapeadas do export, para não perder dado
    importado_em      timestamptz not null default now()
);
create index if not exists ix_nfi_competencia on notas_fiscais_itens(competencia_id);
create index if not exists ix_nfi_cfop on notas_fiscais_itens(cfop);
create index if not exists ix_nfi_ncm on notas_fiscais_itens(ncm);
create index if not exists ix_nfi_tipo on notas_fiscais_itens(tipo_operacao);

-- ============================================================================================
-- LANÇAMENTOS MANUAIS (DIFAL, CIAP, DAE Antecipado — informações que não vêm dos relatórios)
-- ============================================================================================
create table if not exists lancamentos_manuais (
    id                bigserial primary key,
    competencia_id    bigint not null references competencias(id) on delete cascade,
    tipo              text not null check (tipo in (
                          'difal_debito', 'ciap_credito', 'dae_antecipado_credito',
                          'ajuste_cfop_credito', 'ajuste_cfop_debito', 'outro'
                      )),
    cfop_relacionado  integer references cfop(codigo), -- para 'ajuste_cfop_*': CFOPs lançados fora do
                                                          -- fluxo de importação de NF (ex: 1353/1407/1602/
                                                          -- 1933, que aparecem na Rotina 1024 mas não no
                                                          -- relatório de Entrada/Saída — achado de 05/08/2026)
    descricao         text not null,
    valor             numeric(14,2) not null,
    criado_por        uuid references auth.users(id),
    created_at        timestamptz not null default now()
);
create index if not exists ix_lm_competencia on lancamentos_manuais(competencia_id);
comment on column lancamentos_manuais.tipo is
    'ajuste_cfop_credito/débito cobre CFOPs que a Rotina 1024 mostra mas que não vêm no relatório de '
    'Entrada/Saída (lançados direto no sistema contábil) — ex: CFOP 1602 contribuiu R$ 3.814,87 de crédito '
    'em julho/2026 e não aparece em nenhum relatório de NF. Sem esse ajuste, o Checkpoint 1 nunca fecha '
    'para competências que tenham esse tipo de lançamento.';

-- ============================================================================================
-- APURACAO_LINHAS (resultado calculado — linhas 01 a 14 do livro de apuração)
-- ============================================================================================
create table if not exists apuracao_linhas (
    id                bigserial primary key,
    competencia_id    bigint not null references competencias(id) on delete cascade,
    linha             text not null, -- '01'..'14'
    descricao         text not null,
    valor             numeric(14,2) not null default 0,
    detalhe           jsonb,         -- breakdown por CFOP/lançamento, para auditoria da conta
    calculado_em      timestamptz not null default now(),
    unique (competencia_id, linha)
);

-- ============================================================================================
-- CHECKPOINTS (valores de referência digitados das Rotinas 1024/1025, para conferência)
-- ============================================================================================
create table if not exists checkpoints_referencia (
    id                bigserial primary key,
    competencia_id    bigint not null references competencias(id) on delete cascade,
    fonte             text not null check (fonte in ('rotina_1024','rotina_1025')),
    cfop              integer references cfop(codigo),  -- preenchido para fonte = rotina_1024
    linha             text,                              -- preenchido para fonte = rotina_1025 ('01'..'14')
    valor_base        numeric(14,2),
    valor_icms        numeric(14,2),
    created_at        timestamptz not null default now()
);
create index if not exists ix_checkpoints_competencia on checkpoints_referencia(competencia_id);

-- ============================================================================================
-- INCONSISTENCIAS (achados das validações cruzadas — NCM x ST e transferência entre não vinculadas)
-- ============================================================================================
create table if not exists inconsistencias (
    id                bigserial primary key,
    competencia_id    bigint not null references competencias(id) on delete cascade,
    tipo              text not null check (tipo in ('ncm_st_inconsistente','transferencia_nao_vinculada')),
    ncm               text,
    cfop              integer references cfop(codigo),
    nf_item_id        bigint references notas_fiscais_itens(id),
    descricao         text not null,
    status            text not null default 'pendente' check (status in ('pendente','revisado','ignorado')),
    revisado_por      uuid references auth.users(id),
    revisado_em       timestamptz,
    created_at        timestamptz not null default now()
);
create index if not exists ix_inconsistencias_competencia on inconsistencias(competencia_id);
create index if not exists ix_inconsistencias_status on inconsistencias(status);

-- ============================================================================================
-- RLS — todos os usuários autenticados têm o mesmo nível de acesso (decisão de 05/08/2026)
-- ============================================================================================
alter table empresas enable row level security;
alter table cfop enable row level security;
alter table competencias enable row level security;
alter table notas_fiscais_itens enable row level security;
alter table lancamentos_manuais enable row level security;
alter table apuracao_linhas enable row level security;
alter table checkpoints_referencia enable row level security;
alter table inconsistencias enable row level security;

do $$
declare
    t text;
begin
    for t in select unnest(array[
        'empresas','cfop','competencias','notas_fiscais_itens','lancamentos_manuais',
        'apuracao_linhas','checkpoints_referencia','inconsistencias'
    ])
    loop
        -- Postgres não aceita "create policy if not exists" (só existe para tabelas/índices/etc) —
        -- por isso o drop antes do create.
        execute format('drop policy if exists "authenticated_full_access" on %I', t);
        execute format(
            'create policy "authenticated_full_access" on %I '
            'for all to authenticated using (true) with check (true)', t
        );
    end loop;
end $$;
