-- Botão "Zerar Base/Alíquota" na Planilha de Entrada/Saída — pedido do usuário em 06/08/2026: "criar uma
-- forma de selecionar a nota, o cfop e zerar a base de cálculo e a alíquota com uma justificativa". Usa o
-- mesmo histórico de auditoria já criado em sql/010 (auditoria_edicoes_planilha); só falta a coluna de
-- justificativa, que os ajustes normais da grade não usavam.

alter table auditoria_edicoes_planilha
    add column if not exists justificativa text;
comment on column auditoria_edicoes_planilha.justificativa is
    'Preenchida só quando o ajuste veio do botão "Zerar Base/Alíquota" (obrigatória nesse fluxo) — edições '
    'direto na grade não pedem justificativa, então ficam NULL aqui.';
