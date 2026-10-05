# AVISO: Este arquivo contém seletores não confirmados. Precisa preencher com
# seletores reais do Firefly antes de rodar. Ver SELECTORS_GUIDE.md.
"""Registro central e exclusivo dos seletores da UI do Firefly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from .state_reader import ScreenState


SelectorMethod = Literal["role", "text", "label", "css", "test_id", "url_pattern"]


@dataclass(frozen=True, slots=True)
class SelectorDef:
    """Definição auditável: um seletor só participa do fluxo depois de confirmado."""

    method: SelectorMethod
    value: str
    description: str
    confirmed: bool = False
    # A Adobe repete data-testid/id em elementos gêmeos (ex: quadro Primeiro/Último);
    # nth desambigua sem recorrer a XPath frágil.
    nth: int | None = None
    observed_at: str | None = None
    page_context: str | None = None
    deprecated: bool = False
    exact: bool = False


class SupportsLocator(Protocol):
    def get_by_role(self, role: str, **kwargs: object) -> object: ...
    def get_by_text(self, text: str, **kwargs: object) -> object: ...
    def get_by_label(self, text: str, **kwargs: object) -> object: ...
    def get_by_test_id(self, test_id: str) -> object: ...
    def locator(self, selector: str, **kwargs: object) -> object: ...


def _state_selectors() -> dict[ScreenState, SelectorDef]:
    """Import tardio evita dependência circular entre enum e o registro de seletores."""
    from .state_reader import ScreenState

    return {
        ScreenState.LOGGED_OUT: SelectorDef(
            "url_pattern", "TODO:SELETOR", "URL de login, autenticação ou sessão expirada"
        ),
        ScreenState.QUOTA_EXHAUSTED: SelectorDef(
            "text", "TODO:SELETOR", "Aviso de créditos ou quota esgotada"
        ),
        ScreenState.CONTENT_REJECTED: SelectorDef(
            "text",
            "Não foi possível processar esse prompt",
            "Toast ou modal de conteúdo rejeitado",
            True,
        ),
        ScreenState.ERROR_TOAST: SelectorDef(
            "text",
            "Ocorreu um erro",
            "Mensagem terminal do provider quando a geração falha após iniciar",
            True,
            observed_at="2026-08-24",
            page_context="Firefly Generate Video / provider failure panel",
        ),
        ScreenState.RESULT_READY: SelectorDef(
            "css",
            '[data-testid="generate-video-download-button"]:not([aria-disabled="true"])',
            "Botão 'Baixar' do cabeçalho fica sem aria-disabled quando o vídeo termina",
            True,
        ),
        ScreenState.STILL_GENERATING: SelectorDef(
            "text",
            "Gerando vídeo",
            "Texto central confirmado durante geração ativa do Veo/Firefly",
            True,
            observed_at="2026-08-24",
            page_context="Firefly Generate Video / Veo 3.1 Fast active generation screen",
        ),
        ScreenState.UNKNOWN: SelectorDef(
            "css", "TODO:SELETOR", "Fallback automático; não é um estado a confirmar"
        ),
    }


STATE_SELECTORS = _state_selectors()
STATE_PRIORITY = [
    state
    for state in STATE_SELECTORS
    if state.value
    in {
        "logged_out",
        "quota_exhausted",
        "content_rejected",
        "error_toast",
        "result_ready",
        "still_generating",
    }
]

ACTION_SELECTORS: dict[str, SelectorDef] = {
    "generate_video_tab": SelectorDef(
        "text", "TODO:SELETOR", "Entrada Generate Video na homepage ou painel esquerdo"
    ),
    "generate_media_icon": SelectorDef(
        "role", "TODO:SELETOR", "Botão Generate media no painel de configuração"
    ),
    "video_panel": SelectorDef(
        "text", "TODO:SELETOR", "Aba Video dentro do painel Generate media"
    ),
    "model_dropdown": SelectorDef(
        "test_id",
        "firefly-picker-model",
        "Picker Modelo confirmado na tela Gerar vídeo",
        True,
    ),
    "model_dropdown_trigger": SelectorDef(
        "css",
        '[data-testid="firefly-picker-model"] #button',
        "Botão interno do sp-picker de Modelo; o host combobox não abre com click",
        True,
    ),
    "model_option_kling3": SelectorDef(
        "test_id",
        "firefly-menu-item-kling:firefly:colligo:v3direct",
        "Opção Kling 3.0 no seletor de modelo",
        True,
    ),
    "model_option_kling25_turbo": SelectorDef(
        "test_id",
        "firefly-menu-item-ugs:video:kling@kling_v2_5_turbo_pro_i2v",
        "Opcao Kling 2.5 Turbo no seletor de modelo",
        True,
        observed_at="2026-09-01",
        page_context="Firefly Generate Video model picker",
    ),
    "model_option_veo31_fast": SelectorDef(
        "text",
        "Veo 3.1 Fast",
        "Opção Veo 3.1 Fast no seletor de modelo, confirmada pela referência de UI",
        True,
        observed_at="2026-08-20",
        page_context="Firefly Generate Video model picker",
    ),
    "model_option_veo31": SelectorDef(
        "text",
        "Veo 3.1",
        "Opção Veo 3.1 no seletor de modelo, usada como fallback premium quando o Fast retorna 408",
        True,
        observed_at="2026-08-24",
        page_context="Firefly Generate Video model picker",
    ),
    "resolution_dropdown": SelectorDef(
        "test_id",
        "firefly-picker-resolution",
        "Trigger do picker de Resolução (abre a lista; ainda precisa clicar na opção)",
        True,
    ),
    "resolution_dropdown_trigger": SelectorDef(
        "css",
        '[data-testid="firefly-picker-resolution"] #button',
        "Botão interno do sp-picker de Resolução",
        True,
    ),
    "resolution_option_720p": SelectorDef(
        "test_id", "firefly-menu-item-720", "Opção 720p dentro do picker de Resolução", True
    ),
    "resolution_option_1080p": SelectorDef(
        "test_id", "firefly-menu-item-1080", "Opção 1080p dentro do picker de Resolução", True
    ),
    "aspect_ratio_dropdown": SelectorDef(
        "test_id",
        "firefly-picker-aspect-ratio",
        "Trigger do picker de Proporção (abre a lista; ainda precisa clicar na opção)",
        True,
    ),
    "aspect_ratio_dropdown_trigger": SelectorDef(
        "css",
        '[data-testid="firefly-picker-aspect-ratio"] #button',
        "Botão interno do sp-picker de Proporção",
        True,
    ),
    "aspect_ratio_vertical": SelectorDef(
        "test_id",
        "firefly-menu-item-vertical",
        "Opção Vertical (9:16) dentro do picker de Proporção",
        True,
    ),
    "aspect_ratio_widescreen": SelectorDef(
        "test_id",
        "firefly-menu-item-widescreen",
        "Opção Widescreen (16:9) dentro do picker de Proporção",
        True,
    ),
    "duration_dropdown": SelectorDef(
        "test_id",
        "firefly-picker-duration",
        "Picker de Duração na tela Gerar vídeo",
        True,
    ),
    "duration_dropdown_trigger": SelectorDef(
        "css",
        '[data-testid="firefly-picker-duration"] #button',
        "Botão interno do sp-picker de Duração",
        True,
    ),
    "duration_slider": SelectorDef(
        "css",
        '[data-testid="firefly-picker-duration"] [role="slider"], '
        '[data-testid="firefly-picker-duration"] input[type="range"]',
        "Slider de duração usado pelo Kling 3.0 (até 15 segundos)",
        True,
        nth=0,
    ),
    "duration_discrete_option_5": SelectorDef(
        "test_id",
        "firefly-menu-item-5",
        "Opcao observada para Kling 3.0 em 2026-08-10: 5 segundos",
        True,
        observed_at="2026-08-10",
        page_context="Firefly Generate Video / Kling 3.0 duration combobox",
    ),
    "prompt_duration_button": SelectorDef(
        "test_id",
        "prompt-duration-button",
        "Botao compacto de duracao no prompt footer; observado apos selecionar Kling 3.0",
        True,
        observed_at="2026-08-10",
        page_context="Firefly Generate Video / Kling 3.0 prompt footer",
    ),
    "duration_prompt_trigger": SelectorDef(
        "test_id",
        "prompt-duration-button",
        "DurationTrigger: botao compacto do prompt que abre o popover real de duracao por captura",
        True,
        observed_at="2026-08-10",
        page_context="Firefly Generate Video / Kling 3.0 prompt footer",
    ),
    "duration_interceptor_trigger": SelectorDef(
        "css",
        '.click-interceptor[aria-label*="Dura"], .click-interceptor[aria-label*="Duration"]',
        "DurationTrigger alternativo: overlay interceptador semanticamente rotulado como duracao",
        True,
        observed_at="2026-08-10",
        page_context="Firefly Generate Video / Kling 3.0 general settings duration row",
    ),
    "duration_popover": SelectorDef(
        "css",
        "firefly-prompt-duration-popover, sp-popover:has(firefly-duration-slider)",
        "DurationPopover: painel aberto pelo botao de duracao contendo o controle por captura",
        True,
        observed_at="2026-08-10",
        page_context="Firefly Generate Video / Kling 3.0 duration popover",
    ),
    "duration_capture_row": SelectorDef(
        "css",
        "firefly-duration-slider",
        "ShotDurationControl: componente por captura dentro do popover",
        True,
        observed_at="2026-08-10",
        page_context="Firefly Generate Video / Kling 3.0 duration popover",
    ),
    "duration_track": SelectorDef(
        "css",
        'input[data-testid="duration-slider"][aria-label*="Dura"], '
        'input[data-testid="duration-slider"][aria-label*="Duration"]',
        "Track interativo real de duracao; input range visivel dentro do popover",
        True,
        observed_at="2026-08-10",
        page_context="Firefly Generate Video / Kling 3.0 duration popover",
    ),
    "duration_thumb": SelectorDef(
        "css",
        "firefly-duration-slider .handle",
        "Thumb/handle visual do slider de duracao quando exposto no shadow DOM",
        True,
        observed_at="2026-08-10",
        page_context="Firefly Generate Video / Kling 3.0 duration popover",
    ),
    "duration_current_value": SelectorDef(
        "css",
        '[data-testid="firefly-picker-duration"], [data-testid="prompt-duration-button"]',
        "Valores visiveis atuais: trigger geral e trigger compacto de duracao",
        True,
        observed_at="2026-08-10",
        page_context="Firefly Generate Video / Kling 3.0 duration controls",
    ),
    "duration_min_label": SelectorDef(
        "css",
        "firefly-duration-slider .min-label",
        "Label visual minimo do slider de duracao",
        True,
        observed_at="2026-08-10",
        page_context="Firefly Generate Video / Kling 3.0 duration popover",
    ),
    "duration_max_label": SelectorDef(
        "css",
        "firefly-duration-slider .max-label",
        "Label visual maximo do slider de duracao",
        True,
        observed_at="2026-08-10",
        page_context="Firefly Generate Video / Kling 3.0 duration popover",
    ),
    "prompt_duration_slider": SelectorDef(
        "css",
        '[data-testid="prompt-duration-slider"], input[data-testid="duration-slider"][aria-label*="Dura"]',
        "Slider/picker de duracao associado ao prompt quando painel compacto e aberto",
        True,
        observed_at="2026-08-10",
        page_context="Firefly Generate Video / Kling 3.0 prompt duration panel",
    ),
    "first_frame_button": SelectorDef(
        "test_id",
        "placeholder-upload-button",
        "Botão de upload do quadro 'Primeiro' (índice 0; existe um gêmeo para 'Último')",
        True,
        nth=0,
    ),
    "first_frame_editor": SelectorDef(
        "css",
        "firefly-video-generation-keyframes-editor",
        "Host do editor de primeiro e último quadros",
        True,
        observed_at="2026-08-30",
        page_context="Firefly Generate Video / settings panel",
    ),
    "first_frame_upload": SelectorDef(
        "css",
        'input[type="file"]',
        "Input de arquivo oculto do quadro 'Primeiro' (índice 0; id duplicado com o de 'Último')",
        True,
        nth=0,
    ),
    "first_frame_local_file_pt": SelectorDef(
        "text",
        "Seu dispositivo",
        "Opção local em português aberta pelo upload do primeiro quadro",
        True,
        observed_at="2026-08-30",
        page_context="Firefly Generate Video / keyframe upload menu",
        exact=True,
    ),
    "first_frame_local_file_en": SelectorDef(
        "text",
        "Your device",
        "Opção local em inglês aberta pelo upload do primeiro quadro",
        True,
        observed_at="2026-08-30",
        page_context="Firefly Generate Video / keyframe upload menu",
        exact=True,
    ),
    "first_frame_thumbnail": SelectorDef(
        "test_id",
        "remove-frame-0",
        "Botão 'Remover imagem' do quadro 0; só existe depois que o upload realmente terminou",
        True,
    ),
    "prompt_input": SelectorDef(
        "css",
        'textarea[aria-label="Prompt"], .tiptap.ProseMirror[contenteditable="true"]',
        "Campo Prompt: role=textbox+name='Prompt' só funciona ANTES do upload do "
        "first frame. Depois do upload (sempre, nesta automação) o campo vira um "
        "editor rico Tiptap/ProseMirror com aria-label vazio, sem role nem "
        "test-id — CSS de classe da lib é o único gancho estável disponível.",
        True,
    ),
    "generate_button": SelectorDef(
        "test_id",
        "video-generation-generate-button",
        "Botão Gerar. role=button+name='Gerar' resolvia para 2 elementos (o "
        "accordion 'Quadros' também expõe esse nome) — test_id é único.",
        True,
    ),
    "generate_button_host": SelectorDef(
        "css",
        "firefly-video-generation-generate-button",
        "Host web component do botão Gerar; usado para clique por coordenada quando o sp-button interno está em shadow DOM",
        True,
        observed_at="2026-08-24",
        page_context="Firefly Generate Video / prompt panel",
    ),
    "audio_toggle": SelectorDef(
        "role", "switch:Áudio", "Toggle Áudio confirmado na tela Gerar vídeo", True
    ),
    "download_button": SelectorDef(
        "test_id",
        "generate-video-download-button",
        "Botão 'Baixar' do cabeçalho. Confirmado por geração real: clicar nele "
        "dispara o download do .mp4 diretamente — NÃO abre modal de exportação "
        "(o fluxo de modal do export_flow.py original era uma hipótese errada).",
        True,
    ),
    "logged_in_marker": SelectorDef(
        "role", "combobox:Modelo", "Controle Modelo visível confirma a sessão", True
    ),
    "overlay_close_buttons": SelectorDef(
        "role", "TODO:SELETOR", "Botões que fecham overlays não terminais"
    ),
}

# Alguns modelos expõem durações discretas como menu items; Kling 3.0 usa um
# slider. Manter as duas representações no registro permite tratar a UI atual e
# a variante discreta sem construir seletores fora deste módulo.
ACTION_SELECTORS.update(
    {
        f"duration_option_{seconds}": SelectorDef(
            "test_id",
            f"firefly-menu-item-{seconds}",
            f"Opção discreta de duração: {seconds} segundos",
            True,
        )
        for seconds in range(1, 16)
    }
)

# Adaptador de leitura para os módulos posteriores; não cria nenhum seletor adicional.
SELECTORS: dict[str, SelectorDef] = {
    **{state.value: definition for state, definition in STATE_SELECTORS.items()},
    **ACTION_SELECTORS,
}
STATE_SELECTOR_KEYS = tuple(state.value for state in STATE_PRIORITY)


class UnconfirmedSelectorError(RuntimeError):
    """Impede automação baseada em hipótese sobre o DOM da Adobe."""


def locator_for(page: SupportsLocator, key: str) -> object:
    """Monta locator para um seletor confirmado, preservando a chave no diagnóstico."""
    definition = next(
        (
            state_definition
            for state, state_definition in STATE_SELECTORS.items()
            if state.value == key
        ),
        ACTION_SELECTORS.get(key),
    )
    if definition is None:
        raise KeyError(f"selector_key desconhecida: {key}")
    if not definition.confirmed:
        raise UnconfirmedSelectorError(f"selector_key={key} ainda não foi confirmado manualmente")
    locator = _build_locator(page, definition)
    return locator.nth(definition.nth) if definition.nth is not None else locator


def _build_locator(page: SupportsLocator, definition: SelectorDef) -> object:
    if definition.method == "role":
        role, _, name = definition.value.partition(":")
        return page.get_by_role(role, name=name or None)
    if definition.method == "text":
        return page.get_by_text(definition.value, exact=definition.exact)
    if definition.method == "label":
        return page.get_by_label(definition.value, exact=True)
    if definition.method == "test_id":
        return page.get_by_test_id(definition.value)
    if definition.method == "css":
        return page.locator(definition.value)
    raise ValueError(f"url_pattern não cria locator: selector_key={definition.value}")
