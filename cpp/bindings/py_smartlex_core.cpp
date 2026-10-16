#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "smartlex/inverted_index.hpp"
#include "smartlex/tokenizer.hpp"

#include <exception>
#include <string>
#include <vector>

namespace {

/**
 * Python object wrapper around C++ `smartlex::InvertedIndex`.
 */
struct PyNativeIndexObject {
    PyObject_HEAD
    smartlex::InvertedIndex* index;
};

void PyNativeIndex_dealloc(PyNativeIndexObject* self) {
    delete self->index;
    self->index = nullptr;
    Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
}

PyObject* PyNativeIndex_new(PyTypeObject* type, PyObject* /*args*/, PyObject* /*kwds*/) {
    PyNativeIndexObject* self = reinterpret_cast<PyNativeIndexObject*>(type->tp_alloc(type, 0));
    if (self != nullptr) {
        self->index = nullptr;
    }
    return reinterpret_cast<PyObject*>(self);
}

int PyNativeIndex_init(PyNativeIndexObject* self, PyObject* /*args*/, PyObject* /*kwds*/) {
    try {
        delete self->index;
        self->index = new smartlex::InvertedIndex();
        return 0;
    } catch (const std::exception& e) {
        PyErr_SetString(PyExc_RuntimeError, e.what());
        return -1;
    }
}

PyObject* PyNativeIndex_add_document(PyNativeIndexObject* self, PyObject* args, PyObject* kwds) {
    static const char* kwlist[] = {"path", "text", "mtime_ns", "file_size", nullptr};
    const char* path = nullptr;
    const char* text = nullptr;
    unsigned long long mtime_ns = 0;
    unsigned long long file_size = 0;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "ss|KK", const_cast<char**>(kwlist),
                                     &path, &text, &mtime_ns, &file_size)) {
        return nullptr;
    }

    try {
        uint32_t doc_id = 0;
        std::string s_path(path);
        std::string s_text(text);
        Py_BEGIN_ALLOW_THREADS
        doc_id = self->index->add_document(s_path, s_text, mtime_ns, file_size);
        Py_END_ALLOW_THREADS
        return PyLong_FromUnsignedLong(doc_id);
    } catch (const std::exception& e) {
        PyErr_SetString(PyExc_RuntimeError, e.what());
        return nullptr;
    }
}

PyObject* PyNativeIndex_remove_document(PyNativeIndexObject* self, PyObject* args) {
    const char* path = nullptr;
    if (!PyArg_ParseTuple(args, "s", &path)) {
        return nullptr;
    }
    bool removed = self->index->remove_document(path);
    return PyBool_FromLong(removed ? 1 : 0);
}

PyObject* PyNativeIndex_is_up_to_date(PyNativeIndexObject* self, PyObject* args) {
    const char* path = nullptr;
    unsigned long long mtime_ns = 0;
    unsigned long long file_size = 0;
    if (!PyArg_ParseTuple(args, "sKK", &path, &mtime_ns, &file_size)) {
        return nullptr;
    }
    bool up_to_date = self->index->is_up_to_date(path, mtime_ns, file_size);
    return PyBool_FromLong(up_to_date ? 1 : 0);
}

PyObject* PyNativeIndex_search(PyNativeIndexObject* self, PyObject* args, PyObject* kwds) {
    static const char* kwlist[] = {"query", "max_results", nullptr};
    const char* query = nullptr;
    Py_ssize_t max_results = 100;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "s|n", const_cast<char**>(kwlist),
                                     &query, &max_results)) {
        return nullptr;
    }

    try {
        std::vector<smartlex::SearchResult> results;
        std::string s_query(query);
        size_t limit = (max_results > 0) ? static_cast<size_t>(max_results) : 100;

        Py_BEGIN_ALLOW_THREADS
        results = self->index->search(s_query, limit);
        Py_END_ALLOW_THREADS

        PyObject* py_list = PyList_New(static_cast<Py_ssize_t>(results.size()));
        if (!py_list) return nullptr;

        for (size_t i = 0; i < results.size(); ++i) {
            const auto& r = results[i];
            PyObject* matched_words_list = PyList_New(static_cast<Py_ssize_t>(r.matched_words.size()));
            for (size_t j = 0; j < r.matched_words.size(); ++j) {
                PyList_SET_ITEM(matched_words_list, static_cast<Py_ssize_t>(j),
                                PyUnicode_FromString(r.matched_words[j].c_str()));
            }

            PyObject* dict = Py_BuildValue(
                "{s:k, s:s, s:d, s:k, s:k, s:N}",
                "doc_id", static_cast<unsigned long>(r.doc_id),
                "path", r.path.c_str(),
                "score", r.score,
                "matched_terms", static_cast<unsigned long>(r.matched_terms),
                "first_match_pos", static_cast<unsigned long>(r.first_match_pos),
                "matched_words", matched_words_list
            );
            PyList_SET_ITEM(py_list, static_cast<Py_ssize_t>(i), dict);
        }
        return py_list;
    } catch (const std::exception& e) {
        PyErr_SetString(PyExc_RuntimeError, e.what());
        return nullptr;
    }
}

PyObject* PyNativeIndex_search_paths(PyNativeIndexObject* self, PyObject* args, PyObject* kwds) {
    static const char* kwlist[] = {"query", "max_results", nullptr};
    const char* query = nullptr;
    Py_ssize_t max_results = 100;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "s|n", const_cast<char**>(kwlist),
                                     &query, &max_results)) {
        return nullptr;
    }

    try {
        std::vector<smartlex::SearchResult> results;
        std::string s_query(query);
        size_t limit = (max_results > 0) ? static_cast<size_t>(max_results) : 100;

        Py_BEGIN_ALLOW_THREADS
        results = self->index->search(s_query, limit);
        Py_END_ALLOW_THREADS

        PyObject* py_list = PyList_New(static_cast<Py_ssize_t>(results.size()));
        if (!py_list) return nullptr;
        for (size_t i = 0; i < results.size(); ++i) {
            PyList_SET_ITEM(py_list, static_cast<Py_ssize_t>(i),
                            PyUnicode_FromString(results[i].path.c_str()));
        }
        return py_list;
    } catch (const std::exception& e) {
        PyErr_SetString(PyExc_RuntimeError, e.what());
        return nullptr;
    }
}

PyObject* PyNativeIndex_suggest_autocomplete(PyNativeIndexObject* self, PyObject* args, PyObject* kwds) {
    static const char* kwlist[] = {"prefix", "max_words", nullptr};
    const char* prefix = "";
    Py_ssize_t max_words = 100;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|sn", const_cast<char**>(kwlist),
                                     &prefix, &max_words)) {
        return nullptr;
    }

    size_t limit = (max_words > 0) ? static_cast<size_t>(max_words) : 100;
    std::vector<std::string> words = self->index->suggest_autocomplete(prefix, limit);

    PyObject* py_list = PyList_New(static_cast<Py_ssize_t>(words.size()));
    if (!py_list) return nullptr;
    for (size_t i = 0; i < words.size(); ++i) {
        PyList_SET_ITEM(py_list, static_cast<Py_ssize_t>(i),
                        PyUnicode_FromString(words[i].c_str()));
    }
    return py_list;
}

PyObject* PyNativeIndex_get_document_keywords(PyNativeIndexObject* self, PyObject* args, PyObject* kwds) {
    static const char* kwlist[] = {"path", "max_keywords", nullptr};
    const char* path = nullptr;
    Py_ssize_t max_keywords = 150;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "s|n", const_cast<char**>(kwlist),
                                     &path, &max_keywords)) {
        return nullptr;
    }

    size_t limit = (max_keywords > 0) ? static_cast<size_t>(max_keywords) : 150;
    std::vector<std::string> words = self->index->get_document_keywords(path, limit);

    PyObject* py_list = PyList_New(static_cast<Py_ssize_t>(words.size()));
    if (!py_list) return nullptr;
    for (size_t i = 0; i < words.size(); ++i) {
        PyList_SET_ITEM(py_list, static_cast<Py_ssize_t>(i),
                        PyUnicode_FromString(words[i].c_str()));
    }
    return py_list;
}

PyObject* PyNativeIndex_get_all_paths(PyNativeIndexObject* self, PyObject* /*args*/) {
    std::vector<std::string> paths = self->index->get_all_paths();
    PyObject* py_list = PyList_New(static_cast<Py_ssize_t>(paths.size()));
    if (!py_list) return nullptr;
    for (size_t i = 0; i < paths.size(); ++i) {
        PyList_SET_ITEM(py_list, static_cast<Py_ssize_t>(i),
                        PyUnicode_FromString(paths[i].c_str()));
    }
    return py_list;
}

PyObject* PyNativeIndex_save_binary(PyNativeIndexObject* self, PyObject* args) {
    const char* file_path = nullptr;
    if (!PyArg_ParseTuple(args, "s", &file_path)) {
        return nullptr;
    }
    bool ok = false;
    std::string s_path(file_path);
    Py_BEGIN_ALLOW_THREADS
    ok = self->index->save_binary(s_path);
    Py_END_ALLOW_THREADS
    return PyBool_FromLong(ok ? 1 : 0);
}

PyObject* PyNativeIndex_load_binary(PyNativeIndexObject* self, PyObject* args) {
    const char* file_path = nullptr;
    if (!PyArg_ParseTuple(args, "s", &file_path)) {
        return nullptr;
    }
    bool ok = false;
    std::string s_path(file_path);
    Py_BEGIN_ALLOW_THREADS
    ok = self->index->load_binary(s_path);
    Py_END_ALLOW_THREADS
    return PyBool_FromLong(ok ? 1 : 0);
}

PyObject* PyNativeIndex_active_document_count(PyNativeIndexObject* self, PyObject* /*args*/) {
    return PyLong_FromSize_t(self->index->active_document_count());
}

PyObject* PyNativeIndex_vocabulary_size(PyNativeIndexObject* self, PyObject* /*args*/) {
    return PyLong_FromSize_t(self->index->vocabulary_size());
}

#ifndef _PyCFunction_CAST
#define _PyCFunction_CAST(func) reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)()>(func))
#endif

PyMethodDef PyNativeIndex_methods[] = {
    {"add_document", _PyCFunction_CAST(PyNativeIndex_add_document),
     METH_VARARGS | METH_KEYWORDS, "Add or update a document in the inverted index."},
    {"remove_document", _PyCFunction_CAST(PyNativeIndex_remove_document),
     METH_VARARGS, "Mark a document path as deleted (tombstone) in O(1) time."},
    {"is_up_to_date", _PyCFunction_CAST(PyNativeIndex_is_up_to_date),
     METH_VARARGS, "Check if a file path is already indexed with the same mtime_ns and size."},
    {"search", _PyCFunction_CAST(PyNativeIndex_search),
     METH_VARARGS | METH_KEYWORDS, "Execute BM25F + Exact Phrase + Proximity + Fuzzy search."},
    {"search_paths", _PyCFunction_CAST(PyNativeIndex_search_paths),
     METH_VARARGS | METH_KEYWORDS, "Execute search and return ranked file paths."},
    {"suggest_autocomplete", _PyCFunction_CAST(PyNativeIndex_suggest_autocomplete),
     METH_VARARGS | METH_KEYWORDS, "Return top autocomplete words matching optional prefix."},
    {"get_document_keywords", _PyCFunction_CAST(PyNativeIndex_get_document_keywords),
     METH_VARARGS | METH_KEYWORDS, "Return top indexed keywords for a document path."},
    {"get_all_paths", _PyCFunction_CAST(PyNativeIndex_get_all_paths),
     METH_NOARGS, "Return all active document paths in the index."},
    {"save_binary", _PyCFunction_CAST(PyNativeIndex_save_binary),
     METH_VARARGS, "Save compressed binary index (.slex) to disk."},
    {"load_binary", _PyCFunction_CAST(PyNativeIndex_load_binary),
     METH_VARARGS, "Load compressed binary index (.slex) from disk."},
    {"active_document_count", _PyCFunction_CAST(PyNativeIndex_active_document_count),
     METH_NOARGS, "Return number of active indexed documents."},
    {"vocabulary_size", _PyCFunction_CAST(PyNativeIndex_vocabulary_size),
     METH_NOARGS, "Return number of unique terms in the inverted index."},
    {nullptr, nullptr, 0, nullptr}
};

PyTypeObject PyNativeIndexType = []() {
    PyTypeObject t{};
    t.ob_base = {PyObject_HEAD_INIT(nullptr) 0};
    t.tp_name = "_smartlex_core.NativeIndex";
    t.tp_basicsize = sizeof(PyNativeIndexObject);
    t.tp_dealloc = reinterpret_cast<destructor>(PyNativeIndex_dealloc);
    t.tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE;
    t.tp_doc = "C++20 Compressed Positional Inverted Index Engine";
    t.tp_methods = PyNativeIndex_methods;
    t.tp_init = reinterpret_cast<initproc>(PyNativeIndex_init);
    t.tp_new = PyNativeIndex_new;
    return t;
}();

// Module-level helper: tokenize_keywords(text, include_stopwords=False)
PyObject* py_tokenize_keywords(PyObject* /*self*/, PyObject* args, PyObject* kwds) {
    static const char* kwlist[] = {"text", "include_stopwords", nullptr};
    const char* text = nullptr;
    int include_stopwords = 0;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "s|p", const_cast<char**>(kwlist),
                                     &text, &include_stopwords)) {
        return nullptr;
    }

    std::vector<smartlex::Token> tokens = smartlex::Tokenizer::tokenize(text);
    PyObject* py_list = PyList_New(0);
    if (!py_list) return nullptr;

    for (const auto& tok : tokens) {
        if (!include_stopwords && smartlex::Tokenizer::is_stopword(tok.text)) {
            continue;
        }
        PyObject* item = PyUnicode_FromString(tok.text.c_str());
        PyList_Append(py_list, item);
        Py_DECREF(item);
    }
    return py_list;
}

// Module-level helper: stem_word(word)
PyObject* py_stem_word(PyObject* /*self*/, PyObject* args) {
    const char* word = nullptr;
    if (!PyArg_ParseTuple(args, "s", &word)) {
        return nullptr;
    }
    std::string norm = smartlex::Tokenizer::normalize_word(word);
    std::string stemmed = smartlex::Tokenizer::stem_word(norm);
    return PyUnicode_FromString(stemmed.c_str());
}

// Module-level helper: edit_distance(a, b, max_distance=2)
PyObject* py_edit_distance(PyObject* /*self*/, PyObject* args) {
    const char* a = nullptr;
    const char* b = nullptr;
    int max_dist = 2;
    if (!PyArg_ParseTuple(args, "ss|i", &a, &b, &max_dist)) {
        return nullptr;
    }
    int d = smartlex::Tokenizer::edit_distance(a, b, max_dist);
    return PyLong_FromLong(d);
}

PyMethodDef module_methods[] = {
    {"tokenize_keywords", _PyCFunction_CAST(py_tokenize_keywords),
     METH_VARARGS | METH_KEYWORDS, "Tokenize text into normalized keywords using C++20 Tokenizer."},
    {"stem_word", py_stem_word, METH_VARARGS, "Compute Porter-style English stem of a word."},
    {"edit_distance", py_edit_distance, METH_VARARGS, "Compute bounded Levenshtein edit distance."},
    {nullptr, nullptr, 0, nullptr}
};

struct PyModuleDef smartlex_core_module = {
    PyModuleDef_HEAD_INIT,
    "_smartlex_core",
    "SmartLex C++20 Core Search & Indexing Engine",
    -1,
    module_methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr
};

}  // namespace

PyMODINIT_FUNC PyInit__smartlex_core(void) {
    if (PyType_Ready(&PyNativeIndexType) < 0) {
        return nullptr;
    }

    PyObject* m = PyModule_Create(&smartlex_core_module);
    if (m == nullptr) {
        return nullptr;
    }

    Py_INCREF(&PyNativeIndexType);
    if (PyModule_AddObject(m, "NativeIndex", reinterpret_cast<PyObject*>(&PyNativeIndexType)) < 0) {
        Py_DECREF(&PyNativeIndexType);
        Py_DECREF(m);
        return nullptr;
    }

    return m;
}
