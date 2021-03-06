# web_request_handlers.py: Classes deriving from Tornado's web.RequestHandler.
#
# Copyright 2011 James Boyden <jboy@jboy.id.au>
#
# This file is part of Distil.
#
# Distil is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License, version 3, as
# published by the Free Software Foundation.
#
# Distil is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License,
# version 3, for more details.
#
# You should have received a copy of the GNU General Public License,
# version 3, along with this program; if not, see
# http://www.gnu.org/licenses/gpl-3.0.html


import operator
import os
import string
import sys
import types
import tornado.web

from collections import defaultdict
from PIL import Image

import abstract_file_io
import attachments
import authentication
import bibfile_utils
import config
import constants
import filesystem_utils
import form_button_actions
import stored_bibs
import topic_tag_file_io
import wiki_file_io
import wiki_markup


class BaseHandler(tornado.web.RequestHandler):
  def __init__(self, *args, **kwargs):
    tornado.web.RequestHandler.__init__(self, *args, **kwargs)
    self.attachments_subdir_abspath = os.path.join(config.DOCLIB_BASE_ABSPATH, constants.ATTACHMENTS_SUBDIR)
    self.bibs_subdir_abspath = os.path.join(config.DOCLIB_BASE_ABSPATH, constants.BIBS_SUBDIR)
    self.index_dir_abspath = os.path.join(config.DOCLIB_BASE_ABSPATH, constants.TOPIC_TAG_INDEX_SUBDIR)
    self.wiki_subdir_abspath = os.path.join(config.DOCLIB_BASE_ABSPATH, constants.WIKI_SUBDIR)

  def get_doc_attrs(self, cite_key):
    try:
      doc_attrs = stored_bibs.get_doc_attrs(cite_key)
    except filesystem_utils.DirectoryNotFound:
      raise tornado.web.HTTPError(404)

    # Note that, even if no exception was thrown, 'doc_attrs' might have no
    # details about the doc if there was simply no doc stored for that cite-key.
    if doc_attrs.has_key("doc-name"):
      doc_fname = doc_attrs["doc-name"]
      doc_attrs["doc-path"] = "/static/%s/bibs/%s/%s" % (config.DOCLIB_SYMLINK_NAME, cite_key, doc_fname)

    return doc_attrs

  def get_submit_button_pressed(self):
    if not self.get_arguments("submit-button"):
      return None
    return self.get_arguments("submit-button")[0]

  def get_text(self, name, strip=True):
    text_list = self.get_arguments(name, strip)
    # 'text_list' will either be an empty list (if there was no text)
    # or a list containing a single string (the text).
    # Let's convert that to a string (whether empty or not) in all cases...
    return (text_list[0] if text_list else "")

  def get_input_text(self, name):
    return self.get_text(name)

  def get_textarea_text(self, name):
    return self.get_text(name, strip=False).rstrip().replace('\r\n', '\n').replace('\r', '\n')

  def get_current_user(self):
    return self.get_secure_cookie("username")


class MainHandler(BaseHandler):
  @tornado.web.authenticated
  def get(self):
    self.redirect("/cite-keys")


class LoginHandler(BaseHandler):
  def get(self):
    args = dict(
        next=self.get_argument("next", default=""),
        error_message="",
        prev_username="",
    )
    self.render_page(args)

  def render_page(self, kwargs):
    self.render("login.html", **kwargs)

  def post(self):
    username = self.get_argument("username", default="")
    password = self.get_argument("password", default="")
    arg_next = self.get_argument("next", default="")

    if username and password:
      if authentication.can_authenticate_user(username, password):
        self.set_secure_cookie("username", username)

        # Try to redirect back to the original target page.
        if arg_next:
          self.redirect(arg_next)
        else:
          self.redirect("/")
        return

    # If we got to here, authentication was unsuccessful (no username supplied?
    # no password supplied? username and password didn't authenticate?), so we
    # send the user back to "/login" to try again.

    # Retain the Tornado-supplied query string "next=..." (if present) that
    # remembers where to go when the user finally authenticates successfully.
    args = dict(
        # Note that we're being very careful with the (lack of a) 'url_escape'
        # in the next line...  (compare with "templates/login.html")
        next=arg_next,
        error_message="The username or password you entered is incorrect.",
        prev_username=username,
    )
    self.render_page(args)


class LogoutHandler(BaseHandler):
  def get(self):
    self.clear_cookie("username")
    self.redirect("/login")


CREATE_ATTACHMENT_FORM_FIELD_NAMES = [
    "filename",
    "dirpath",
    "new-filename",
    "short-descr",
    "source-url",
]

class AttachmentsHandler(BaseHandler):
  @tornado.web.authenticated
  def get(self):
    self.render_page()

  def post(self):
    if not self.get_arguments("submit-button"):
      self.redirect("/attachments")

    fields = extract_attachment_form_fields(self)
    if "filename" not in fields:
      self.render_page(fields, "Please supply the filename of the attachment")
      return

    try:
      attachment_id = attachments.store_new_attachment_incl_dirpath(**fields)
      self.redirect("/attachment/%s" % attachment_id)
    except attachments.Error as e:
      error_msg = str(e)
      self.render_page(fields, error_msg)
    except filesystem_utils.Error as e:
      error_msg = str(e)
      self.render_page(fields, error_msg)

  def render_page(self, fields={}, error_msg=""):
    def convert_to_variable_name(s):
      return s.replace('-', '_')

    attachments_with_attrs = self.get_attachments_with_attrs()

    create_attachment_form_params = dict(
        post_action_url="/attachments",
        error_msg=error_msg
    )
    # Re-populate the fields with the values that were passed in by the user.
    for fn in CREATE_ATTACHMENT_FORM_FIELD_NAMES:
      fn_var_name = convert_to_variable_name(fn)
      fn_var_name_init = "%s_init" % fn_var_name
      if fn_var_name in fields:
        create_attachment_form_params[fn_var_name_init] = fields[fn_var_name]
      else:
        create_attachment_form_params[fn_var_name_init] = ""

    self.render("attachments.html", title="Attachments", items=attachments_with_attrs,
        create_attachment_form_params=create_attachment_form_params)

  def get_attachments_with_attrs(self):
    filesystem_utils.ensure_dir_exists(self.attachments_subdir_abspath)
    all_attachment_dirnames = os.listdir(self.attachments_subdir_abspath)
    attachments_with_attrs = [attachments.get_attachment_attrs(dirname)
        for dirname in all_attachment_dirnames]

    # We want the attachment index to be sorted primarily by the
    # human-readable filename (compared case-INSENSITIVELY), and
    # secondarily by the unique attachment ID (to ensure that if there
    # are duplicate human-readable filenames, their relative ordering
    # will be stable).
    return sorted(
        sorted(attachments_with_attrs, key=lambda t: t[1]),
            key=lambda t: t[0].lower())


def extract_attachment_form_fields(calling_obj):
  def extract_elem_from_list_if_present(the_list):
    if len(the_list):
      return the_list[0]
    else:
      return None

  def convert_to_variable_name(s):
    return s.replace('-', '_')

  extracted_form_fields = {}
  for fn in CREATE_ATTACHMENT_FORM_FIELD_NAMES:
    field_val = extract_elem_from_list_if_present(calling_obj.get_arguments(fn))
    if field_val:
      extracted_form_fields[convert_to_variable_name(fn)] = field_val

  return extracted_form_fields


class CiteKeyListBaseHandler(BaseHandler):
  def __init__(self, *args, **kwargs):
    BaseHandler.__init__(self, *args, **kwargs)

    def remove_punctuation(s):
      return string.translate(s, None, string.punctuation)

    def make_option_value(s):
      words = remove_punctuation(s).lower().split()
      return "-".join(words)

    # When there are new options added, simply insert the (text, functions) to this list.
    # Everything else will update automatically.
    option_text_and_functions = [
      ("Cite Key",                                      [operator.itemgetter(0)]),
      ("Date Imported (Oldest First)",                  date_added_getter()),
      ("Date Imported (Newest First)",                  date_added_getter(True)),
      ("Year Published (Oldest First), then Cite Key",  year_published_getter()),
      ("Year Published (Newest First), then Cite Key",  year_published_getter(True)),
    ]

    list_of_triples = [(make_option_value(text), text, func)
        for (text, func) in option_text_and_functions]

    self.order_by_choices_and_functions = dict([(option_value, func)
        for (option_value, func, func) in list_of_triples])
    self.order_by_choices_and_text = [(option_value, text)
        for (option_value, text, func) in list_of_triples]

  def get_cite_keys_and_attrs(self, cite_key):
    attrs = self.get_doc_attrs(cite_key)
    attrs["topic-tags"] = topic_tag_file_io.get_topic_tags_for_cite_key(cite_key)
    return (cite_key, attrs)

  def sort_cite_keys_and_attrs(self, cite_keys_and_attrs, order_by):
    sorting_keys = self.order_by_choices_and_functions[order_by]
    for k in sorting_keys:
      if type(k) == types.TupleType:
        # There was a 'reverse' parameter provided too.
        cite_keys_and_attrs.sort(key=k[0], reverse=k[1])
      else:
        cite_keys_and_attrs.sort(key=k)

  def get_order_by_choice(self):
    if not self.get_arguments("reload-button"):
      return None
    return self.get_arguments("order-by-choice")[0]


def year_published_getter(reverse=False):
  def get_year_published(obj):
    return obj[1]["year-published"]
  return [operator.itemgetter(0), (get_year_published, reverse)]


def date_added_getter(reverse=False):
  def get_date_added(obj):
    return obj[1]["date-added"]
  return [(get_date_added, reverse)]


class CiteKeysHandler(CiteKeyListBaseHandler):
  @tornado.web.authenticated
  def get(self):
    self.render_page()

  @tornado.web.authenticated
  def post(self):
    order_by_choice = self.get_order_by_choice()
    self.render_page(order_by_choice)

  def render_page(self, order_by="cite-key"):
    filesystem_utils.ensure_dir_exists(self.bibs_subdir_abspath)
    cite_keys = os.listdir(self.bibs_subdir_abspath)
    cite_keys_and_attrs = map(self.get_cite_keys_and_attrs, cite_keys)
    self.sort_cite_keys_and_attrs(cite_keys_and_attrs, order_by)

    self.render("cite-keys.html", title="Cite Keys", items=cite_keys_and_attrs,
        choices_and_text=self.order_by_choices_and_text, order_by_choice=order_by)


class TagXHandler(CiteKeyListBaseHandler):
  @tornado.web.authenticated
  def get(self, topic_tag):
    self.render_page(topic_tag)

  @tornado.web.authenticated
  def post(self, topic_tag):
    order_by_choice = self.get_order_by_choice()
    self.render_page(topic_tag, order_by_choice)

  def filter_by_tags(self, cite_keys_and_attrs, topic_tag):
    if self.get_arguments("shta"):
      filtered_by_tags = set(self.get_arguments("shta"))
      # Remove the current topic-tag from every topic-tag list.
      filtered_by_tags.add(topic_tag)

      # Remove any element of 'cite_keys_and_attrs' which is not tagged with
      # all the tags in 'filtered_by_tags'.
      filtered = []
      for cite_key, attrs in cite_keys_and_attrs:
        if attrs.has_key("topic-tags"):
          topic_tags = set(attrs["topic-tags"])
          if filtered_by_tags <= topic_tags:
            # We keep this item.
            # But first, remove these tags from being displayed.
            topic_tags -= filtered_by_tags
            attrs["topic-tags"] = list(topic_tags)
            attrs["topic-tags"].sort()
            filtered.append((cite_key, attrs))
        else:
          # Since this item has no topic tags, there's no way it can have even
          # the current topic-tag, so of course we won't keep it.
          pass
      cite_keys_and_attrs = filtered
    else:
      # Remove the current topic-tag from every topic-tag list.
      for cite_key, attrs in cite_keys_and_attrs:
        if attrs.has_key("topic-tags"):
          attrs["topic-tags"].remove(topic_tag)

  def render_page(self, topic_tag, order_by="cite-key"):
    topic_tag_index_fname_abspath = os.path.join(self.index_dir_abspath, topic_tag)
    if not os.path.exists(topic_tag_index_fname_abspath):
      raise tornado.web.HTTPError(404)

    cite_keys = topic_tag_file_io.read_topic_tag_index(topic_tag_index_fname_abspath)
    cite_keys_and_attrs = map(self.get_cite_keys_and_attrs, cite_keys)
    self.filter_by_tags(cite_keys_and_attrs, topic_tag)
    self.sort_cite_keys_and_attrs(cite_keys_and_attrs, order_by)

    self.render("tag-x.html", title=topic_tag, items=cite_keys_and_attrs,
        choices_and_text=self.order_by_choices_and_text, order_by_choice=order_by,
        topic_tag=topic_tag, filter_by_tags=self.get_arguments("shta"))


KNOWN_IMAGE_FILENAME_SUFFIXES = ['PNG', 'JPG', 'JPEG', 'GIF']

class AttachmentXHandler(BaseHandler):
  @tornado.web.authenticated
  def get(self, dirname):
    self.render_page(dirname)

  def render_page(self, dirname):
    dirname_abspath = os.path.join(self.attachments_subdir_abspath, dirname)
    if not os.path.exists(dirname_abspath):
      raise tornado.web.HTTPError(404)
    (fname, dirname, fsize, descr, source_url, suffix, ftype, static_path) = \
        attachments.get_attachment_attrs(dirname)

    is_image = False
    img_width = None
    img_height = None
    if ftype in KNOWN_IMAGE_FILENAME_SUFFIXES:
      try:
        # If this next line throws an exception, the file does not contain
        # a recognised image format.
        fname_abspath = os.path.join(dirname_abspath, fname)
        img = Image.open(fname_abspath)

        is_image = True
        img_width, img_height = img.size
      except IOError as e:
        # Not a recognised image format
        pass

    self.render("attachment-x.html", title=fname,
      dirname=dirname,
      fsize=fsize,
      descr=descr,
      source_url=source_url,
      suffix=suffix,
      ftype=ftype,
      is_image=is_image,
      img_width=img_width,
      img_height=img_height,
      img_preview_width=750,
      static_path=static_path)


class BibXHandler(BaseHandler):
  defaultdict_render_page_args = defaultdict(str)

  # These instances provide callback functions that are connected to buttons.
  notes_wiki_button_actions = \
      form_button_actions.WikiButtonActions("Notes", "notes",
          wiki_file_io.get_notes_for_cite_key,
          wiki_file_io.update_notes_for_cite_key)
  tag_button_actions = form_button_actions.TagButtonActions()

  # When you press a button for one set of inputs (for example, you press
  # the wiki Save button), you want to retain any as-yet-unsaved changes
  # for any other sets of inputs (for example, tags).
  # Hence, we cross-register the "save any unsaved changes" functions for
  # each different set of inputs.
  notes_wiki_button_actions.other_unsaved_form_data_to_retain.append(tag_button_actions.retain_unsaved_tags)
  tag_button_actions.other_unsaved_form_data_to_retain.append(notes_wiki_button_actions.retain_unsaved_wiki_text)

  @tornado.web.authenticated
  def get(self, cite_key):
    self.render_page(cite_key, BibXHandler.defaultdict_render_page_args)

  def render_page(self, cite_key, args):
    cite_key_dir_abspath = os.path.join(self.bibs_subdir_abspath, cite_key)
    if not os.path.exists(cite_key_dir_abspath):
      raise tornado.web.HTTPError(404)

    bib_fname_abspath = os.path.join(cite_key_dir_abspath, cite_key + ".bib")
    multiple_bib_entries = bibfile_utils.read_entries_from_file(bib_fname_abspath)
    # FIXME:  Should check that length of 'multiple_bib_entries' is exactly 1.

    all_topic_tags = topic_tag_file_io.get_all_topic_tags()
    tags_for_this_cite_key = args["tags"] or topic_tag_file_io.get_topic_tags_for_cite_key(cite_key)
    tags = map(lambda t: (t, (t in tags_for_this_cite_key)), all_topic_tags)

    notes_with_wiki_markup = args["notes"] or wiki_file_io.get_notes_for_cite_key(cite_key)
    wiki_markup_lines = notes_with_wiki_markup.split('\n')
    notes_with_html_markup = []
    wiki_markup_error = None
    try:
      notes_with_html_markup = wiki_markup.read_wiki_lines_and_transform(wiki_markup_lines, {})
    except wiki_markup.InputSyntaxError as e:
      (args["notes_message"], wiki_markup_error) = format_wiki_markup_errors(e, wiki_markup_lines)
      args["notes_message_class"] = "message-error"

    notes_params = dict(
        div_id="edit-notes",
        message=args["notes_message"],
        message_class=args["notes_message_class"],
        wiki_markup_error=wiki_markup_error,
        wiki_raw=notes_with_wiki_markup,
        wiki_html=notes_with_html_markup,
        wiki_change_descr=args["notes_change_descr"],
        wiki_area_name="notes",
        wiki_area_title="Notes")

    self.render("bib-x.html", title=cite_key, cite_key=cite_key,
        bib_entries=multiple_bib_entries[0], doc_attrs=self.get_doc_attrs(cite_key),
        notes_params=notes_params, tags=tags, new_tags=args["new_tags"],
        tags_message=args["tags_message"], tags_message_class=args["tags_message_class"],
        abstract=abstract_file_io.get_abstract_for_cite_key(cite_key))

  @tornado.web.authenticated
  def post(self, cite_key):
    args_to_pass_to_render_page = BibXHandler.defaultdict_render_page_args.copy()
    submit_buttons = {
      "Preview Notes": BibXHandler.notes_wiki_button_actions.preview_wiki_text,
      "Reset Notes": BibXHandler.notes_wiki_button_actions.reset_wiki_text,
      "Save Notes": BibXHandler.notes_wiki_button_actions.save_wiki_text,
      "Save Tags": BibXHandler.tag_button_actions.save_tags,
    }
    submit_button_pressed = self.get_submit_button_pressed()
    if submit_button_pressed:
      try:
        submit_buttons[submit_button_pressed](self, cite_key, args_to_pass_to_render_page)
      except KeyError as e:
        # There is a new submit button which we need to add to the 'submit_buttons' dictionary.
        sys.stderr.write("Error in %s, class %s: unhandled submit button '%s'" %
            (__file__, self.__class__.__name__, submit_button_pressed))

    self.render_page(cite_key, args_to_pass_to_render_page)


class WikiXHandler(BaseHandler):
  defaultdict_render_page_args = defaultdict(str)

  # These instances provide callback functions that are connected to buttons.
  text_wiki_button_actions = \
      form_button_actions.WikiButtonActions("Text", "text",
          wiki_file_io.get_text_for_wiki_page,
          wiki_file_io.update_text_for_wiki_page)

  @tornado.web.authenticated
  def get(self, wiki_word):
    self.render_page(wiki_word, WikiXHandler.defaultdict_render_page_args)

  def render_page(self, wiki_word, args):
    wiki_fname_abspath = os.path.join(self.wiki_subdir_abspath, wiki_word, wiki_word + constants.WIKI_FNAME_SUFFIX)
    if not os.path.exists(wiki_fname_abspath):
      self.render("wiki-x-not-found.html", title=wiki_word, wiki_word=wiki_word)
      return

    text_with_wiki_markup = args["text"] or wiki_file_io.get_text_for_wiki_page(wiki_word)
    wiki_markup_lines = text_with_wiki_markup.split('\n')
    text_with_html_markup = []
    wiki_markup_error = None
    try:
      text_with_html_markup = wiki_markup.read_wiki_lines_and_transform(wiki_markup_lines, {})
    except wiki_markup.InputSyntaxError as e:
      (args["text_message"], wiki_markup_error) = format_wiki_markup_errors(e, wiki_markup_lines)
      args["text_message_class"] = "message-error"

    text_params = dict(
        div_id="edit-text",
        message=args["text_message"],
        message_class=args["text_message_class"],
        wiki_markup_error=wiki_markup_error,
        wiki_raw=text_with_wiki_markup,
        wiki_html=text_with_html_markup,
        wiki_change_descr=args["text_change_descr"],
        wiki_area_name="text",
        wiki_area_title="Text")

    self.render("wiki-x.html", title=wiki_word, wiki_word=wiki_word, text_params=text_params)

  @tornado.web.authenticated
  def post(self, wiki_word):
    wiki_word = wiki_markup.normalise_string_for_wiki_word(wiki_word)
    args_to_pass_to_render_page = WikiXHandler.defaultdict_render_page_args.copy()
    submit_buttons = {
      "Create Page": self.create_wiki_page,
      "Preview Text": WikiXHandler.text_wiki_button_actions.preview_wiki_text,
      "Reset Text": WikiXHandler.text_wiki_button_actions.reset_wiki_text,
      "Save Text": WikiXHandler.text_wiki_button_actions.save_wiki_text,
    }
    submit_button_pressed = self.get_submit_button_pressed()
    if submit_button_pressed:
      try:
        submit_buttons[submit_button_pressed](self, wiki_word, args_to_pass_to_render_page)
      except KeyError as e:
        # There is a new submit button which we need to add to the 'submit_buttons' dictionary.
        sys.stderr.write("Error in %s, class %s: unhandled submit button '%s'" %
            (__file__, self.__class__.__name__, submit_button_pressed))

    self.render_page(wiki_word, args_to_pass_to_render_page)

  def create_wiki_page(self, handler, wiki_word, render_page_args):
    """Ensure the wiki-word directory and wiki-word file exist.
    
    Will also create the wiki-subdir if it doesn't already exist.
    """
    wiki_file_io.create_wiki_page(self.wiki_subdir_abspath, wiki_word)


class WikiCreateHandler(BaseHandler):
  @tornado.web.authenticated
  def post(self):
    page_name_specified = self.get_argument("wiki-word", default="")
    wiki_word = wiki_markup.normalise_string_for_wiki_word(page_name_specified)
    if not wiki_word:
      self.redirect("/wiki-words")
    else:
      submit_buttons = {
        "Create": self.create_wiki_page,
      }
      submit_button_pressed = self.get_submit_button_pressed()
      if submit_button_pressed:
        try:
          # FIXME:  Convert unsafe arbitrary text to a safe wiki-word.
          submit_buttons[submit_button_pressed](self, wiki_word)
          self.redirect("/wiki/%s" % wiki_word)
        except KeyError as e:
          # There is a new submit button which we need to add to the 'submit_buttons' dictionary.
          sys.stderr.write("Error in %s, class %s: unhandled submit button '%s'" %
              (__file__, self.__class__.__name__, submit_button_pressed))
      else:
        self.redirect("/wiki-words")

  def create_wiki_page(self, handler, wiki_word):
    """Ensure the wiki-word directory and wiki-word file exist.
    
    Will also create the wiki-subdir if it doesn't already exist.
    """
    wiki_file_io.create_wiki_page(self.wiki_subdir_abspath, wiki_word)


class WikiWordsHandler(BaseHandler):
  @tornado.web.authenticated
  def get(self):
    filesystem_utils.ensure_dir_exists(self.wiki_subdir_abspath)
    titles = os.listdir(self.wiki_subdir_abspath)
    titles.sort()

    self.render("wiki-words.html", title="Wiki Words", items=titles)


def format_wiki_markup_errors(e, wiki_input_lines):
  error_msg = "Wiki markup error: line %d: %s" % (e.line_num, e.message)
  before_erroneous_text = e.text[:e.text_start]
  erroneous_text = e.text[e.text_start:e.text_end]
  after_erroneous_text = e.text[e.text_end:]

  return (error_msg,
      (wiki_input_lines[:e.line_num - 1],  # Don't forget that the line_num starts at 1, not 0.
      before_erroneous_text, erroneous_text, after_erroneous_text,
      wiki_input_lines[e.line_num:]))  # Don't forget that the line_num starts at 1, not 0.

