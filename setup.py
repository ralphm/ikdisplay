#!/usr/bin/env python

from setuptools import setup
from ikdisplay import __version__

# Make sure 'twisted' doesn't appear in top_level.txt

try:
    from setuptools.command import egg_info
    egg_info.write_toplevel_names
except (ImportError, AttributeError):
    pass
else:
    def _top_level_package(name):
        return name.split('.', 1)[0]

    def _hacked_write_toplevel_names(cmd, basename, filename):
        pkgs = dict.fromkeys(
            [_top_level_package(k)
                for k in cmd.distribution.iter_distribution_names()
                if _top_level_package(k) != "twisted"
            ]
        )
        cmd.write_file("top-level names", filename, '\n'.join(pkgs) + '\n')

    egg_info.write_toplevel_names = _hacked_write_toplevel_names

setup(name='ikdisplay',
      version=__version__,
      description='IkDisplay',
      author='Ralph Meijer',
      author_email='ralphm@ik.nu',
      url='https://github.com/mediamatic/ikdisplay',
      license='MIT',
      packages=[
          'ikdisplay',
          'ikdisplay.web',
          'twisted.plugins',
      ],
      package_data={
          '': [
              'web/*.html',
              'web/static/*.css',
              'web/static/*.tpl',
              'web/static/js/*.js',
              ],
          'twisted.plugins': [
               'twisted/plugins/ikdisplayaggregator.py',
               ],
          },
      zip_safe=False,
      install_requires=[
          'Twisted',
          'wokkel',
          'Axiom',
          'lxml',
          'twittytwister',
          ],
)
