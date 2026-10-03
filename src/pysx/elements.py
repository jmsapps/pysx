"""HTML element markers.

Plain strings, so `styled(div, ...)` and `styled("div", ...)` are equivalent.
"""

div = "div"
span = "span"
section = "section"
nav = "nav"
form = "form"
label = "label"
strong = "strong"
button = "button"
input = "input"
ul = "ul"
li = "li"
p = "p"
h1 = "h1"
br = "br"

VOID = frozenset({"br", "hr", "img", "input", "meta", "link"})
