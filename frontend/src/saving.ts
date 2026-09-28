import type { SaveState } from "./protocol";

// Transport ACKs and server acceptance do not confirm a disk write.
export function savingStatus(save: SaveState | null, connected: boolean, localChanges: boolean) {
  if (!connected) return { text: "salvamento não confirmado", error: false,
    detail: "Aguarde a reconexão para confirmar o salvamento.", retry: false };
  if (save?.status === "error") return { text: "falha ao salvar", error: true,
    detail: `${save.error ?? "A gravação falhou."} Mantenha o servidor aberto e tente novamente.`, retry: true };
  if (localChanges) return { text: "alterações pendentes", error: false,
    detail: "Há alterações aguardando confirmação ou conflitos para resolver.", retry: false };
  if (!save) return { text: "aguardando confirmação", error: false,
    detail: "O servidor ainda não confirmou o estado do arquivo.", retry: false };
  if (save.status !== "saved" || save.saved_revision !== save.revision) return { text: "salvando…", error: false,
    detail: "As últimas alterações ainda não foram gravadas.", retry: false };
  return { text: "salvo", error: false, detail: "As alterações aceitas foram gravadas no arquivo.", retry: false };
}
