# Note: Work in progress

import os
import re
import codecs
import textwrap
from xml.sax.saxutils import escape as html_escape
from StringIO import StringIO

import Version
from Code import CCodeWriter
from Cython import Utils


class AnnotationCCodeWriter(CCodeWriter):

    def __init__(self, create_from=None, buffer=None, copy_formatting=True):
        CCodeWriter.__init__(self, create_from, buffer, copy_formatting=True)
        if create_from is None:
            self.annotation_buffer = StringIO()
            self.annotations = []
            self.last_pos = None
            self.code = {}
        else:
            # When creating an insertion point, keep references to the same database
            self.annotation_buffer = create_from.annotation_buffer
            self.annotations = create_from.annotations
            self.code = create_from.code
            self.last_pos = create_from.last_pos

    def create_new(self, create_from, buffer, copy_formatting):
        return AnnotationCCodeWriter(create_from, buffer, copy_formatting)

    def write(self, s):
        CCodeWriter.write(self, s)
        self.annotation_buffer.write(s)

    def mark_pos(self, pos):
        if pos is not None:
            CCodeWriter.mark_pos(self, pos)
        if self.last_pos:
            pos_code = self.code.setdefault(self.last_pos[0].filename, {})
            code = pos_code.get(self.last_pos[1], "")
            pos_code[self.last_pos[1]] = code + self.annotation_buffer.getvalue()
        self.annotation_buffer = StringIO()
        self.last_pos = pos

    def annotate(self, pos, item):
        self.annotations.append((pos, item))

    def _css(self):
        """css template will later allow to choose a colormap"""
        css = [self._css_template]
        for i in range(255):
            color = u"FFFF%02x" % int(255/(1+i/10.0))
            css.append('\n.cython.score-%d {background-color: #%s;}' % (i, color))
        try:
            from pygments.formatters import HtmlFormatter
            css.append(HtmlFormatter().get_style_defs('.cython'))
        except ImportError:
            pass
        return ''.join(css)

    _js = """
    function toggleDiv(id) {
        theDiv = id.nextElementSibling
        if (theDiv.style.display != 'block') theDiv.style.display = 'block';
        else theDiv.style.display = 'none';
    }
    """.strip()

    _css_template = textwrap.dedent("""
        body.cython { font-family: courier; font-size: 12; }

        .cython.tag  {  }
        .cython.line { margin: 0em }
        .cython.code  { font-size: 9; color: #444444; display: none; margin: 0px 0px 0px 20px;  }

        .cython.code .py_c_api  { color: red; }
        .cython.code .py_macro_api  { color: #FF7000; }
        .cython.code .pyx_c_api  { color: #FF3000; }
        .cython.code .pyx_macro_api  { color: #FF7000; }
        .cython.code .refnanny  { color: #FFA000; }
        .cython.code .error_goto  { color: #FFA000; }

        .cython.code .coerce  { color: #008000; border: 1px dotted #008000 }
        .cython.code .py_attr { color: #FF0000; font-weight: bold; }
        .cython.code .c_attr  { color: #0000FF; }
        .cython.code .py_call { color: #FF0000; font-weight: bold; }
        .cython.code .c_call  { color: #0000FF; }
    """)

    def save_annotation(self, source_filename, target_filename):
        with Utils.open_source_file(source_filename) as f:
            code = f.read()
        generated_code = self.code.get(source_filename, {})
        c_file = Utils.decode_filename(os.path.basename(target_filename))
        html_filename = os.path.splitext(target_filename)[0] + ".html"
        with codecs.open(html_filename, "w", encoding="UTF-8") as out_buffer:
            out_buffer.write(self._save_annotation(code, generated_code, c_file))

    def _save_annotation_header(self, c_file):
        outlist = [
            textwrap.dedent(u'''\
            <!DOCTYPE html>
            <!-- Generated by Cython {watermark} -->
            <html>
            <head>
                <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
                <style type="text/css">
                {css}
                </style>
                <script>
                {js}
                </script>
            </head>
            <body class="cython">
            <p>Generated by Cython {watermark}</p>
            ''').format(css=self._css(), js=self._js, watermark=Version.watermark)
        ]
        if c_file:
            outlist.append(u'<p>Raw output: <a href="%s">%s</a></p>\n' % (c_file, c_file))
        return outlist

    def _save_annotation_footer(self):
        return (u'</body></html>\n',)

    def _save_annotation(self, code, generated_code, c_file=None):
        """
        lines : original cython source code split by lines
        generated_code : generated c code keyed by line number in original file
        target filename : name of the file in which to store the generated html
        c_file : filename in which the c_code has been written
        """
        outlist = []
        outlist.extend(self._save_annotation_header(c_file))
        outlist.extend(self._save_annotation_body(code, generated_code))
        outlist.extend(self._save_annotation_footer())
        return ''.join(outlist)

    def _htmlify_code(self, code):
        try:
            from pygments import highlight
            from pygments.lexers import CythonLexer
            from pygments.formatters import HtmlFormatter
        except ImportError:
            # no Pygments, just escape the code
            return html_escape(code)

        html_code = highlight(
            code, CythonLexer(stripnl=False, stripall=False),
            HtmlFormatter(nowrap=True))
        return html_code

    def _save_annotation_body(self, cython_code, generated_code):
        outlist = [u'<div class="cython">']
        pos_comment_marker = u'/* \N{HORIZONTAL ELLIPSIS} */\n'
        new_calls_map = dict(
            (name, 0) for name in
            'refnanny py_macro_api py_c_api pyx_macro_api pyx_c_api error_goto'.split()
        ).copy

        self.mark_pos(None)

        def annotate(match):
            group_name = match.lastgroup
            calls[group_name] += 1
            return ur"<span class='%s'>%s</span>" % (
                group_name, match.group(group_name))

        lines = self._htmlify_code(cython_code).splitlines()
        lineno_width = len(str(len(lines)))

        for k, line in enumerate(lines, 1):
            try:
                c_code = generated_code[k]
            except KeyError:
                c_code = ''
            else:
                c_code = _replace_pos_comment(pos_comment_marker, c_code)
                if c_code.startswith(pos_comment_marker):
                    c_code = c_code[len(pos_comment_marker):]
                c_code = html_escape(c_code)

            calls = new_calls_map()
            c_code = _parse_code(annotate, c_code)
            score = (5 * calls['py_c_api'] + 2 * calls['pyx_c_api'] +
                     calls['py_macro_api'] + calls['pyx_macro_api'])

            if c_code:
                onclick = " onclick='toggleDiv(this)'"
                expandsymbol = '+'
            else:
                onclick = ''
                expandsymbol = '&#xA0;'

            outlist.append(
                u"<pre class='cython line score-{score}'{onclick}>"
                # generate line number with expand symbol in front,
                # and the right  number of digit
                u"{expandsymbol}{line:0{lineno_width}d}: {code}</pre>\n".format(
                    score=score,
                    expandsymbol=expandsymbol,
                    lineno_width=lineno_width,
                    line=k,
                    code=line.rstrip(),
                    onclick=onclick,
                ))
            if c_code:
                outlist.append(u"<pre class='cython code score-%s'>%s</pre>" % (score, c_code))
        outlist.append(u"</div>")
        return outlist


_parse_code = re.compile(
    ur'(?P<refnanny>__Pyx_X?(?:GOT|GIVE)REF|__Pyx_RefNanny[A-Za-z]+)|'
    ur'(?:'
    ur'(?P<pyx_macro_api>__Pyx_[A-Z][A-Z_]+)|'
    ur'(?P<pyx_c_api>__Pyx_[A-Z][a-z_][A-Za-z_]+)|'
    ur'(?P<py_macro_api>Py[A-Z][a-z]+_[A-Z][A-Z_]+)|'
    ur'(?P<py_c_api>Py[A-Z][a-z]+_[A-Z][a-z][A-Za-z_]+)'
    ur')(?=\()|'       # look-ahead to exclude subsequent '(' from replacement
    ur'(?P<error_goto>(?:(?<=;) *if .* +)?\{__pyx_filename = .*goto __pyx_L\w+;\})'
).sub


_replace_pos_comment = re.compile(
    # this matches what Cython generates as code line marker comment
    ur'^\s*/\*(?:(?:[^*]|\*[^/])*\n)+\s*\*/\s*\n',
    re.M
).sub


class AnnotationItem(object):

    def __init__(self, style, text, tag="", size=0):
        self.style = style
        self.text = text
        self.tag = tag
        self.size = size

    def start(self):
        return u"<span class='cython tag %s' title='%s'>%s" % (self.style, self.text, self.tag)

    def end(self):
        return self.size, u"</span>"
