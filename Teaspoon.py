import sys 
import os
import re
import functools
import sublime
import string
import sublime_plugin

folder = os.path.realpath('lib')
if folder not in sys.path:
     sys.path.insert(0, folder)

from slimit.parser import Parser
from slimit import ast

class ShowInPanel:
  def __init__(self, window):
    self.window = window

  def display_results(self):
    self.panel = self.window.get_output_panel("exec")
    self.window.run_command("show_panel", {"panel": "output.exec"})
    self.panel.settings().set("color_scheme", THEME)
    self.panel.set_syntax_file(SYNTAX)
    if HIDE_PANEL:
      self.window.run_command("hide_panel")
    self.panel.settings().set("color_scheme", THEME)


class ShowInScratch:
  def __init__(self, window):
    self.window = window
    self.active_for = 0
    self.copied_until = 0

  def display_results(self):
    self.panel = self.window.get_output_panel("exec")
    self.window.run_command("hide_panel")
    self.view = self.window.open_file("Test Results")
    self.view.set_scratch(True)
    self.view.set_read_only(False)

    self.view.set_syntax_file(SYNTAX)
    self.view.settings().set("color_scheme", THEME)
    self.view.set_read_only(True)
    self.poll_copy()
    self.append('\n\n')

  def poll_copy(self):
    # FIXME HACK: Stop polling after one minute
    if self.active_for < 60000:
      self.active_for += 50
      sublime.set_timeout(self.copy_stuff, 50)

  def append(self, content):
    self.view.set_read_only(False)
    edit = self.view.begin_edit()
    self.view.insert(edit, self.view.size(), content)
    self.view.end_edit(edit)
    self.view.set_read_only(True)
    self.view.set_viewport_position((self.view.size(), self.view.size()), True)

  def copy_stuff(self):
    size = self.panel.size()
    content = self.panel.substr(sublime.Region(self.copied_until, size))
    if content:
      self.copied_until = size
      self.append(content)
    self.poll_copy()

class ShowPanels:
  def __init__(self, window):
    self.window = window

  def split(self):
    self.window.run_command('set_layout', {
                          "cols": [0.0, 0.5, 1.0],
                          "rows": [0.0, 1.0],
                          "cells": [[0, 0, 1, 1], [1, 0, 2, 1]]
                      })
    self.window.focus_group(1)

class TestMethodMatcher(object):
  def __init__(self):
    self.matchers = [TestMethodMatcher.UnitTest]
  def find_first_match_in(self, test_file_content, pos):
    for matcher in self.matchers:
      test_name = matcher.find_first_match(test_file_content, pos)
      if test_name:
        return test_name

  class UnitTest(object):
    MARK = "'sublime-text-2-teaspoon'"

    @staticmethod
    def insert_mark(text, pos):
      dpos = text.rfind("describe(", 0, pos)
      ipos = text.rfind("it(", 0, pos)
      pos = max(dpos, ipos)
      pattern = re.compile("function\s*\(\s*\)\s*{")
      match = pattern.search(text, pos)
      if match:
        return text[:match.end()] + TestMethodMatcher.UnitTest.MARK + text[match.end():]
      return None

    @staticmethod
    def get_test_method_name(node):
      if isinstance(node, ast.FunctionCall) and isinstance(node.identifier, ast.Identifier) and (node.identifier.value == 'describe' or node.identifier.value == 'it'):
          return node.args[0].value.strip('\'"')
      else:
        return None

    @staticmethod
    def is_test_method(node):
      return isinstance(node, ast.String) and node.value == TestMethodMatcher.UnitTest.MARK

    @staticmethod
    def get_test_name(node, path = ''):
      for sub_node in node:
        if TestMethodMatcher.UnitTest.is_test_method(sub_node):
          return path.strip(' ')
        else:
          name = TestMethodMatcher.UnitTest.get_test_method_name(sub_node)
          name = TestMethodMatcher.UnitTest.get_test_name(sub_node, path + ' ' + name if name else path)
          if name:
            return name 
      return None
      
    @staticmethod
    def find_first_match(test_file_content, pos):
      test_file_content = TestMethodMatcher.UnitTest.insert_mark(test_file_content, pos)
      if not test_file_content:
        return None
      parser = Parser()
      tree = parser.parse(test_file_content)
      return TestMethodMatcher.UnitTest.get_test_name(tree)


class TeaspoonTestSettings:
  def __init__(self):
    self.settings = sublime.load_settings("Teaspoon.sublime-settings")

  def __getattr__(self, name):
    if not self.settings.has(name):
      raise AttributeError(name)
    value = sublime.active_window().active_view().settings().get(name)
    if value:
      return lambda **kwargs: value.format(**kwargs)
    return lambda **kwargs: self.settings.get(name).format(**kwargs)


class BaseTeaspoonTask(sublime_plugin.TextCommand):
  def load_config(self):
    s = sublime.load_settings("Teaspoon.sublime-settings")
    global TEST_FOLDER; TEST_FOLDER = s.get("test_folder")
    global USE_SCRATCH; USE_SCRATCH = s.get("ruby_use_scratch")
    global IGNORED_DIRECTORIES; IGNORED_DIRECTORIES = s.get("ignored_directories")
    global HIDE_PANEL; HIDE_PANEL = s.get("hide_panel")
    global BEFORE_CALLBACK; BEFORE_CALLBACK = s.get("before_callback")
    global AFTER_CALLBACK; AFTER_CALLBACK = s.get("after_callback")
    global COMMAND_PREFIX; COMMAND_PREFIX = False
    global SAVE_ON_RUN; SAVE_ON_RUN = s.get("save_on_run")
    global SYNTAX; SYNTAX = s.get('syntax')
    global THEME; THEME = s.get('theme')
    global TERMINAL_ENCODING; TERMINAL_ENCODING = s.get('terminal_encoding')


    rbenv   = s.get("check_for_rbenv")
    rvm     = s.get("check_for_rvm")
    bundler = s.get("check_for_bundler")
    spring  = s.get("check_for_spring")
    if rbenv or rvm: self.rbenv_or_rvm(s, rbenv, rvm)
    if spring: self.spring_support()
    if bundler: self.bundler_support()

  def spring_support(self):
    global COMMAND_PREFIX
    COMMAND_PREFIX = COMMAND_PREFIX + " spring "

  def rbenv_or_rvm(self, s, rbenv, rvm):
    which = os.popen('which rbenv').read().split('\n')[0]
    brew = '/usr/local/bin/rbenv'
    rbenv_cmd = os.path.expanduser('~/.rbenv/bin/rbenv')
    rvm_cmd = os.path.expanduser('~/.rvm/bin/rvm-auto-ruby')

    if os.path.isfile(brew): rbenv_cmd = brew
    elif os.path.isfile(which): rbenv_cmd = which

    global COMMAND_PREFIX
    if rbenv and self.is_executable(rbenv_cmd):
      COMMAND_PREFIX = rbenv_cmd + ' exec'
    elif rvm and self.is_executable(rvm_cmd):
      COMMAND_PREFIX = rvm_cmd + ' -S'

  def bundler_support(self):
    project_root = self.file_type(None, False).find_project_root()
    if not os.path.isdir(project_root):
      s = sublime.load_settings("Teaspoon.last-run")
      project_root = s.get("last_test_working_dir")

    gemfile_path = project_root + '/Gemfile'

    global COMMAND_PREFIX
    if not COMMAND_PREFIX:
      COMMAND_PREFIX = ""

    if os.path.isfile(gemfile_path):
      COMMAND_PREFIX =  COMMAND_PREFIX + " bundle exec "

  def save_all(self):
    if SAVE_ON_RUN:
      self.window().run_command("save_all")

  def is_executable(self, path):
    return os.path.isfile(path) and os.access(path, os.X_OK)

  def save_test_run(self, command, working_dir):
    s = sublime.load_settings("Teaspoon.last-run")
    s.set("last_test_run", command)
    s.set("last_test_working_dir", working_dir)

    sublime.save_settings("Teaspoon.last-run")

  def run_shell_command(self, command, working_dir):
    if not command:
      return False
    if BEFORE_CALLBACK:
      os.system(BEFORE_CALLBACK)
    if AFTER_CALLBACK:
      command += " ; " + AFTER_CALLBACK
    self.save_test_run(command, working_dir)
    if COMMAND_PREFIX:
      command = COMMAND_PREFIX + ' ' + command
    if int(sublime.version().split('.')[0]) <= 2:
      command = [command]
    self.view.window().run_command("exec", {
      "cmd": command,
      "shell": True,
      "working_dir": working_dir,
      "file_regex": r"([^ ]*\.rb):?(\d*)",
      "encoding": TERMINAL_ENCODING
    })
    self.display_results()
    return True

  def display_results(self):
    display = ShowInScratch(self.window()) if USE_SCRATCH else ShowInPanel(self.window())
    display.display_results()

  def window(self):
    return self.view.window()

  class BaseFile(object):
    def __init__(self, file_name, partition_folder=""):
      self.folder_name, self.file_name = os.path.split(file_name)
      self.partition_folder = partition_folder
      self.absolute_path = file_name
    def parent_dir_name(self):
      head_dir, tail_dir = os.path.split(self.folder_name)
      return tail_dir
    def run_all_tests_command(self): return None
    def get_project_root(self): return self.folder_name
    def find_project_root(self):
      to_find = os.sep + self.partition_folder + os.sep
      project_root, _, _ = self.absolute_path.partition(to_find)
      return project_root
    def relative_file_path(self):
      to_find = os.sep + self.partition_folder + os.sep
      _, _, relative_path = self.absolute_path.partition(to_find)
      return self.partition_folder + os.sep + relative_path
    def get_current_line_number(self, view):
      char_under_cursor = view.sel()[0].a
      return view.rowcol(char_under_cursor)[0] + 1
    def features(self): return []

  class AnonymousFile(BaseFile):
    def __init__(self):
      True

  class UnitFile(BaseFile):
    def run_all_tests_command(self): return TeaspoonTestSettings().run_teaspoon_unit_command(relative_path=self.relative_file_path())
    def run_single_test_command(self, view):
      text_string = view.substr(sublime.Region(0, 99999))
      test_name = TestMethodMatcher().find_first_match_in(text_string, view.sel()[0].begin())
      if test_name is None:
        sublime.error_message("No test name!")
        return None
      return TeaspoonTestSettings().run_single_teaspoon_unit_command(relative_path=self.relative_file_path(), test_name=test_name, line_number=self.get_current_line_number(view))
    def features(self): return ["run_test"]
    def get_project_root(self): return self.find_project_root()

  def find_partition_folder(self, file_name, default_partition_folder):
    folders = self.view.window().folders()
    file_name = file_name.replace("\\","\\\\")
    for folder in folders:
      folder = folder.replace("\\","\\\\")
      if re.search(folder, file_name):
        return re.sub(os.sep + '.+', "", file_name.replace(folder,"")[1:])
    return default_partition_folder

  def file_type(self, file_name = None, load_config = True):
    if load_config:
      self.load_config()
    file_name = file_name or self.view.file_name()
    if not file_name: return BaseTeaspoonTask.AnonymousFile()
    if re.search(TEST_FOLDER + '.+\.js', file_name):
      partition_folder = self.find_partition_folder(file_name, TEST_FOLDER)
      return BaseTeaspoonTask.UnitFile(file_name, partition_folder)
    else:
      return BaseTeaspoonTask.BaseFile(file_name)


class RunSingleTeaspoonTest(BaseTeaspoonTask):
  def is_enabled(self): return 'run_test' in self.file_type().features()
  def run(self, args):
    self.load_config()
    self.save_all()
    file = self.file_type()
    command = file.run_single_test_command(self.view)
    self.run_shell_command(command, file.get_project_root())


class RunAllTeaspoonTest(BaseTeaspoonTask):
  def is_enabled(self): return 'run_test' in self.file_type().features()
  def run(self, args):
    self.load_config()
    self.save_all()
    file = self.file_type(self.view.file_name())
    command = file.run_all_tests_command()
    if self.run_shell_command(command, file.get_project_root()):
      pass
    else:
      sublime.error_message("Only *_test.rb, test_*.rb, *_spec.rb, *.feature files supported!")

class RunLastTeaspoonTest(BaseTeaspoonTask):
  def load_last_run(self):
    self.load_config()
    self.save_all()
    s = sublime.load_settings("Teaspoon.last-run")
    return (s.get("last_test_run"), s.get("last_test_working_dir"))

  def run(self, args):
    last_command, working_dir = self.load_last_run()
    self.run_shell_command(last_command, working_dir)

class ShowTestPanel(BaseTeaspoonTask):
  def run(self, args):
    self.window().run_command("show_panel", {"panel": "output.exec"})
