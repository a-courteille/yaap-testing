#!/usr/bin/env python
# -*- coding: utf-8 -*-

# (c) 2025 Your Name
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = '''
    callback: ansiboard
    type: notification
    short_description: Envoie les informations des plays à une API
    version_added: "2.9"
    description:
        - Ce plugin de callback envoie les détails des plays Ansible à une API REST.
        - Il enregistre les informations sur le playbook, les tâches, les résultats par hôte et les statistiques.
    options:
        api_url:
            description: URL de l'API pour l'envoi des données
            env:
                - name: ANSIBLE_ansiboard_URL
            ini:
                - section: callback_ansiboard
                  key: api_url
            required: True
        timeout:
            description: Timeout pour les requêtes API (en secondes)
            env:
                - name: ANSIBLE_ansiboard_TIMEOUT
            ini:
                - section: callback_ansiboard
                  key: timeout
            default: 30
            type: int
'''

import json
import uuid
import time
import datetime
import socket
import os
import sys
import platform

import requests

from ansible.plugins.callback import CallbackBase
from ansible import constants as C
from ansible import context
from ansible.utils.display import Display

display = Display()


class CallbackModule(CallbackBase):
    """
    Plugin de callback Ansible qui envoie les informations des plays à une API.
    """
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'notification'
    CALLBACK_NAME = 'ansiboard'
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self, display=None):
        super(CallbackModule, self).__init__(display)
        self.play_id = str(uuid.uuid4())
        self.play_name = None
        self.playbook_name = None
        self.playbook_path = None
        self.hosts = []
        self.current_task = None
        self.tasks = []
        self.task_start_time = None
        self.play_start_time = None
        self.results = {}
        self.api_url = None
        self.timeout = 30
        self.extra_vars = {}
        self.hostname = socket.gethostname()
        
        # Structure pour stocker les résultats de tâches par hôte
        self.task_results = {}
        
    def set_options(self, task_keys=None, var_options=None, direct=None):
        super(CallbackModule, self).set_options(task_keys=task_keys, var_options=var_options, direct=direct)
        
        self.api_url = self.get_option('api_url')
        self.timeout = self.get_option('timeout')
        
        if not self.api_url:
            display.warning("API URL non définie pour le plugin ansiboard. Les données ne seront pas envoyées.")

    def _get_extra_vars(self):
        """Récupère les variables extra_vars passées à ansible-playbook"""
        extra_vars = {}
        if context.CLIARGS.get('extra_vars'):
            for var in context.CLIARGS.get('extra_vars'):
                if isinstance(var, dict):
                    extra_vars.update(var)
        return extra_vars

    def v2_playbook_on_start(self, playbook):
        """Appelé lorsqu'un playbook démarre."""
        self.playbook_path = playbook._file_name
        self.playbook_name = os.path.basename(self.playbook_path)
        self.extra_vars = self._get_extra_vars()
        
    def v2_playbook_on_play_start(self, play):
        """Appelé lorsqu'un play démarre."""
        self.play_start_time = time.time()
        self.play_name = play.get_name()
        self.hosts = [h.get_name() for h in play.get_hosts()]
        
        # Réinitialisation des résultats pour ce play
        self.tasks = []
        self.results = {}
        for host in self.hosts:
            self.results[host] = {
                'ok': 0,
                'changed': 0,
                'unreachable': 0,
                'failed': 0,
                'skipped': 0,
                'rescued': 0,
                'ignored': 0
            }
            
        display.display(f"API Reporter: Début du play '{self.play_name}' avec l'ID {self.play_id}", color=C.COLOR_DEBUG)

    def v2_playbook_on_task_start(self, task, is_conditional):
        """Appelé lorsqu'une tâche démarre."""
        self.task_start_time = time.time()
        task_id = str(uuid.uuid4())
        self.current_task = {
            'task_id': task_id,
            'name': task.get_name(),
            'start_time': datetime.datetime.utcnow().isoformat(),
            'end_time': None,
            'status': 'running',
            'duration': 0,
            'host_results': {}
        }
        
    def v2_runner_on_ok(self, result):
        """Appelé lorsqu'une tâche se termine avec succès."""
        host = result._host.get_name()
        self.results[host]['ok'] += 1
        if result._result.get('changed', False):
            self.results[host]['changed'] += 1
            
        self._process_result(result, 'success')
        
    def v2_runner_on_failed(self, result, ignore_errors=False):
        """Appelé lorsqu'une tâche échoue."""
        host = result._host.get_name()
        if ignore_errors:
            self.results[host]['ignored'] += 1
        else:
            self.results[host]['failed'] += 1
            
        self._process_result(result, 'failed')
        
    def v2_runner_on_unreachable(self, result):
        """Appelé lorsqu'un hôte est injoignable."""
        host = result._host.get_name()
        self.results[host]['unreachable'] += 1
        self._process_result(result, 'unreachable')
        
    def v2_runner_on_skipped(self, result):
        """Appelé lorsqu'une tâche est ignorée."""
        host = result._host.get_name()
        self.results[host]['skipped'] += 1
        self._process_result(result, 'skipped')
        
    def v2_runner_on_rescued(self, result):
        """Appelé lorsqu'une tâche est récupérée après échec."""
        host = result._host.get_name()
        self.results[host]['rescued'] += 1
        self._process_result(result, 'rescued')
        
    def _process_result(self, result, status):
        """Traite un résultat de tâche."""
        host = result._host.get_name()
        task_result = result._result.copy()
        
        # Suppression de données trop volumineuses ou non sérialisables
        for key in ['diff', 'exception', 'module_stderr', 'module_stdout', 'warnings']:
            if key in task_result:
                del task_result[key]
                
        # Nettoyage des données pour la sérialisation JSON
        for key, value in list(task_result.items()):
            if isinstance(value, (set, frozenset)):
                task_result[key] = list(value)
                
        # Stockage du résultat pour cet hôte
        if self.current_task:
            if 'host_results' not in self.current_task:
                self.current_task['host_results'] = {}
                
            duration = None
            if 'duration' in task_result:
                duration = task_result['duration']
                
            self.current_task['host_results'][host] = {
                'status': status,
                'changed': task_result.get('changed', False),
                'duration': duration,
                'ansible_facts': task_result.get('ansible_facts', {}),
                'stdout': task_result.get('stdout', ''),
                'stderr': task_result.get('stderr', '')
            }
            
    def v2_playbook_on_stats(self, stats):
        """Appelé à la fin d'un playbook avec les statistiques."""
        end_time = time.time()
        play_duration = end_time - self.play_start_time if self.play_start_time else 0
        
        # Finalisez la tâche en cours si elle existe
        if self.current_task:
            self.current_task['end_time'] = datetime.datetime.utcnow().isoformat()
            self.current_task['duration'] = time.time() - self.task_start_time if self.task_start_time else 0
            self.current_task['status'] = 'completed'
            self.tasks.append(self.current_task)
            self.current_task = None
            
        # Créez le rapport complet
        report = {
            'play_id': self.play_id,
            'start_time': datetime.datetime.fromtimestamp(self.play_start_time).isoformat() if self.play_start_time else None,
            'end_time': datetime.datetime.fromtimestamp(end_time).isoformat(),
            'status': 'success',  # Déterminé par la présence d'échecs
            'duration': play_duration,
            'playbook': {
                'name': self.playbook_name,
                'path': self.playbook_path
            },
            'inventory': {
                'hosts': self.hosts
            },
            'play': {
                'name': self.play_name,
                'hosts': self.hosts
            },
            'tasks': self.tasks,
            'stats': self.results,
            'extra_vars': self.extra_vars,
            'execution_environment': {
                'ansible_version': self._get_ansible_version(),
                'python_version': platform.python_version(),
                'controller_hostname': self.hostname,
                'user': self._get_current_user()
            }
        }
        
        # Déterminez le statut global du play
        for host_stats in self.results.values():
            if host_stats['failed'] > 0 or host_stats['unreachable'] > 0:
                report['status'] = 'failed'
                break
                
        # Envoyez le rapport à l'API
        self._send_report(report)
        
    def _send_report(self, report):
        """Envoie le rapport à l'API."""
        if not self.api_url or not self.api_token:
            display.warning("URL de l'API ou token manquant. Le rapport ne sera pas envoyé.")
            return
            
        try:
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.api_token}'
            }
            
            response = requests.post(
                self.api_url,
                json=report,
                headers=headers,
                timeout=self.timeout
            )
            
            if response.status_code >= 200 and response.status_code < 300:
                display.display(f"API Reporter: Rapport envoyé avec succès pour le play '{self.play_name}'", color=C.COLOR_OK)
            else:
                display.error(f"API Reporter: Échec de l'envoi du rapport. Statut: {response.status_code}, Réponse: {response.text}")
                
        except Exception as e:
            display.error(f"API Reporter: Erreur lors de l'envoi du rapport: {str(e)}")
            
    def _get_ansible_version(self):
        """Récupère la version d'Ansible."""
        try:
            from ansible.release import __version__
            return __version__
        except ImportError:
            return "inconnu"
            
    def _get_current_user(self):
        """Récupère l'utilisateur courant."""
        try:
            import getpass
            return getpass.getuser()
        except (ImportError, KeyError):
            return "inconnu"