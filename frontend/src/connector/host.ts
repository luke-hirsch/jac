/**
 * The Claude app as this page sees it: the tool call it was opened for, and the host's context
 * (theme, display mode, size). The handlers go on before `connect()`: the host may send the
 * tool result right after the handshake, and a handler set later misses it.
 */
import {
  App,
  applyDocumentTheme,
  applyHostFonts,
  applyHostStyleVariables,
  type McpUiHostContext,
} from "@modelcontextprotocol/ext-apps";
import { errorText, type ToolResult } from "./payload";

export type HostState = {
  connected: boolean;
  error: string | null; // the handshake failed
  pending: boolean; // the call's arguments arrived, its result hasn't
  cancelled: boolean;
  result: ToolResult | null;
  context: McpUiHostContext;
};

export type HostStore = {
  subscribe: (listener: () => void) => () => void;
  get: () => HostState;
};

function applyLook(ctx: McpUiHostContext) {
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  if (ctx.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts);
}

export function connectHost(app: App): HostStore {
  let state: HostState = {
    connected: false,
    error: null,
    pending: false,
    cancelled: false,
    result: null,
    context: {},
  };
  const listeners = new Set<() => void>();
  const set = (patch: Partial<HostState>) => {
    state = { ...state, ...patch };
    listeners.forEach((l) => l());
  };

  app.ontoolinput = () => set({ pending: true });
  app.ontoolresult = (result) =>
    set({ result: result as ToolResult, pending: false });
  app.ontoolcancelled = () => set({ cancelled: true, pending: false });
  app.onhostcontextchanged = (ctx) => {
    applyLook(ctx);
    set({ context: { ...state.context, ...ctx } });
  };
  app.onteardown = async () => ({});

  app.connect().then(
    () => {
      const ctx = app.getHostContext() ?? {};
      applyLook(ctx);
      set({ connected: true, context: { ...ctx, ...state.context } });
    },
    (err) => set({ error: errorText(err) }),
  );

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    get: () => state,
  };
}
