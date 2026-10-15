package com.muninn.core

/**
 * Minimal JSON for the wire bodies, sim protocol and game payloads.
 * Values: null, Boolean, Long, Double, String, List<Any?>, Map<String, Any?>.
 * Integral numbers decode to Long (Python's json emits ints for ints).
 */
object Json {
    fun parse(s: String): Any? = Parser(s).run {
        val v = value()
        ws()
        if (i != s.length) throw IllegalArgumentException("trailing data at $i")
        v
    }

    @Suppress("UNCHECKED_CAST")
    fun obj(s: String): Map<String, Any?> = parse(s) as? Map<String, Any?>
        ?: throw IllegalArgumentException("not a JSON object")

    fun write(v: Any?): String = StringBuilder().also { write(it, v) }.toString()

    private fun write(sb: StringBuilder, v: Any?) {
        when (v) {
            null -> sb.append("null")
            is Boolean -> sb.append(v)
            is Int, is Long, is Short, is Byte -> sb.append(v.toString())
            is Float, is Double -> {
                val d = (v as Number).toDouble()
                if (d.isNaN() || d.isInfinite()) sb.append("null")
                else if (d == Math.floor(d) && Math.abs(d) < 1e15) sb.append(d.toLong())
                else sb.append(d)
            }
            is Number -> sb.append(v.toString())
            is String -> str(sb, v)
            is Map<*, *> -> {
                sb.append('{')
                var first = true
                for ((k, value) in v) {
                    if (!first) sb.append(',')
                    first = false
                    str(sb, k.toString())
                    sb.append(':')
                    write(sb, value)
                }
                sb.append('}')
            }
            is Iterable<*> -> {
                sb.append('[')
                var first = true
                for (x in v) {
                    if (!first) sb.append(',')
                    first = false
                    write(sb, x)
                }
                sb.append(']')
            }
            is Array<*> -> write(sb, v.toList())
            is IntArray -> write(sb, v.toList())
            else -> str(sb, v.toString())
        }
    }

    private fun str(sb: StringBuilder, s: String) {
        sb.append('"')
        for (c in s) {
            when (c) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                '\b' -> sb.append("\\b")
                '\u000c' -> sb.append("\\f")
                else -> if (c < ' ') sb.append(String.format("\\u%04x", c.code)) else sb.append(c)
            }
        }
        sb.append('"')
    }

    private class Parser(val s: String) {
        var i = 0

        fun ws() {
            while (i < s.length && s[i].isWhitespace()) i++
        }

        fun value(): Any? {
            ws()
            if (i >= s.length) throw IllegalArgumentException("unexpected end")
            return when (val c = s[i]) {
                '{' -> obj()
                '[' -> arr()
                '"' -> string()
                't' -> lit("true", true)
                'f' -> lit("false", false)
                'n' -> lit("null", null)
                else -> if (c == '-' || c.isDigit()) num() else throw IllegalArgumentException("bad char '$c' at $i")
            }
        }

        fun lit(word: String, v: Any?): Any? {
            if (!s.startsWith(word, i)) throw IllegalArgumentException("bad literal at $i")
            i += word.length
            return v
        }

        fun obj(): Map<String, Any?> {
            i++
            val m = LinkedHashMap<String, Any?>()
            ws()
            if (s[i] == '}') { i++; return m }
            while (true) {
                ws()
                val k = string()
                ws()
                if (s[i] != ':') throw IllegalArgumentException("expected : at $i")
                i++
                m[k] = value()
                ws()
                when (s[i++]) {
                    ',' -> continue
                    '}' -> return m
                    else -> throw IllegalArgumentException("expected , or } at ${i - 1}")
                }
            }
        }

        fun arr(): List<Any?> {
            i++
            val l = ArrayList<Any?>()
            ws()
            if (s[i] == ']') { i++; return l }
            while (true) {
                l.add(value())
                ws()
                when (s[i++]) {
                    ',' -> continue
                    ']' -> return l
                    else -> throw IllegalArgumentException("expected , or ] at ${i - 1}")
                }
            }
        }

        fun string(): String {
            if (s[i] != '"') throw IllegalArgumentException("expected string at $i")
            i++
            val sb = StringBuilder()
            while (true) {
                val c = s[i++]
                when (c) {
                    '"' -> return sb.toString()
                    '\\' -> {
                        when (val e = s[i++]) {
                            '"' -> sb.append('"'); '\\' -> sb.append('\\'); '/' -> sb.append('/')
                            'b' -> sb.append('\b'); 'f' -> sb.append('\u000c'); 'n' -> sb.append('\n')
                            'r' -> sb.append('\r'); 't' -> sb.append('\t')
                            'u' -> { sb.append(s.substring(i, i + 4).toInt(16).toChar()); i += 4 }
                            else -> throw IllegalArgumentException("bad escape \\$e")
                        }
                    }
                    else -> sb.append(c)
                }
            }
        }

        fun num(): Any {
            val start = i
            if (s[i] == '-') i++
            while (i < s.length && s[i].isDigit()) i++
            var frac = false
            if (i < s.length && s[i] == '.') { frac = true; i++; while (i < s.length && s[i].isDigit()) i++ }
            if (i < s.length && (s[i] == 'e' || s[i] == 'E')) {
                frac = true; i++
                if (s[i] == '+' || s[i] == '-') i++
                while (i < s.length && s[i].isDigit()) i++
            }
            val t = s.substring(start, i)
            return if (frac) t.toDouble() else t.toLongOrNull() ?: t.toDouble()
        }
    }
}

// --- convenience accessors -------------------------------------------------

fun Map<String, Any?>.str(k: String, def: String = ""): String = (this[k] as? String) ?: def
fun Map<String, Any?>.long(k: String, def: Long = 0): Long = (this[k] as? Number)?.toLong() ?: def
fun Map<String, Any?>.int(k: String, def: Int = 0): Int = (this[k] as? Number)?.toInt() ?: def
fun Map<String, Any?>.bool(k: String): Boolean = when (val v = this[k]) {
    is Boolean -> v
    is Number -> v.toInt() != 0
    else -> false
}
@Suppress("UNCHECKED_CAST")
fun Map<String, Any?>.list(k: String): List<Any?> = (this[k] as? List<Any?>) ?: emptyList()

fun ByteArray.hex(): String = joinToString("") { "%02x".format(it.toInt() and 0xff) }
fun String.unhex(): ByteArray {
    require(length % 2 == 0) { "odd hex length" }
    return ByteArray(length / 2) { substring(2 * it, 2 * it + 2).toInt(16).toByte() }
}
