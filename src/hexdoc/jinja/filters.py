from jinja2 import pass_context
from jinja2.runtime import Context
from markupsafe import Markup

from hexdoc.core import Properties, ResourceLocation
from hexdoc.minecraft import I18n
from hexdoc.minecraft.assets.textures import PNGTexture, TextureLookup
from hexdoc.patchouli import Book, FormatTree
from hexdoc.plugin import PluginManager
from hexdoc.utils import cast_or_raise


def hexdoc_wrap(value: str, *args: str):
    tag, *attributes = args
    if attributes:
        attributes = " " + " ".join(attributes)
    else:
        attributes = ""
    return Markup(f"<{tag}{attributes}>{Markup.escape(value)}</{tag}>")


# aliased as _() and _f() at render time
def hexdoc_localize(
    key: str,
    *,
    do_format: bool,
    props: Properties,
    book: Book,
    i18n: I18n,
    allow_missing: bool,
    pm: PluginManager,
):
    # get the localized value from i18n
    localized = i18n.localize(key, allow_missing=allow_missing)

    if not do_format:
        return Markup(localized.value)

    # construct a FormatTree from the localized value (to allow using patchi styles)
    formatted = FormatTree.format(
        localized.value,
        book_id=book.id,
        i18n=i18n,
        macros=book.macros,
        is_0_black=props.is_0_black,
        pm=pm,
    )
    return formatted


# TODO: support the full texture lookup
@pass_context
def hexdoc_texture(context: Context, id: str | ResourceLocation) -> str:
    try:
        textures = cast_or_raise(context["png_textures"], TextureLookup[PNGTexture])
        id = ResourceLocation.model_validate(id)
        return textures[id].url
    except Exception as e:
        e.add_note(f"id:\n    {id}")
        raise
