from __future__ import print_function, unicode_literals

from rbtools.clients.errors import MergeError, PushError
from rbtools.commands import Command, CommandError, Option, RB_MAIN
from rbtools.utils.commands import (build_rbtools_cmd_argv,
                                    extract_commit_message,
                                    get_review_request)
from rbtools.utils.console import confirm
from rbtools.utils.process import execute
from rbtools.utils.review_request import (get_draft_or_current_value,
                                          guess_existing_review_request_id)


class Land(Command):
    """Land changes from a review request onto the remote repository.

    This command takes a review request, applies it to a feature branch,
    merges it with the specified destination branch, and pushes the
    changes to an upstream repository.

    Notes:
        The review request needs to be approved first.

        ``--local`` option can be used to skip the patching step.
    """
    name = 'land'
    author = 'The Review Board Project'
    args = '[<branch-name>]'
    option_list = [
        Option(
            '--dest',
            dest='destination_branch',
            default=None,
            help='Specifies the destination branch to land changes on.'),
        Option(
            '-r', '--review-request-id',
            dest='rid',
            metavar='ID',
            default=None,
            help='Specifies the review request ID.'),
        Option(
            '--local',
            dest='is_local',
            action='store_true',
            default=None,
            help='Forces the change to be merged without patching, if '
                 'merging a local branch. Defaults to true unless '
                 '--review-request-id is used.'),
        Option(
            '--squash',
            dest='squash',
            action='store_true',
            default=False,
            help='Squashes history into a single commit'),
        Command.server_options,
        Command.repository_options,
    ]

    def patch(self, review_request_id):
        patch_command = [RB_MAIN, 'patch']
        patch_command.extend(build_rbtools_cmd_argv(self.options))
        patch_command.append(review_request_id)
        print(execute(patch_command))

    def main(self, branch_name=None, *args):
        """Run the command."""
        self.cmd_args = list(args)

        repository_info, self.tool = self.initialize_scm_tool(
            client_name=self.options.repository_type)
        server_url = self.get_server_url(repository_info, self.tool)
        api_client, api_root = self.get_api(server_url)
        self.setup_tool(self.tool, api_root=api_root)

        # Check if repository info on reviewboard server match local ones.
        repository_info = repository_info.find_server_repository_info(api_root)

        if not self.tool.can_merge or not self.tool.can_push_upstream:
            raise CommandError(
                "This command does not support %s repositories."
                % self.tool.name)

        if self.options.rid:
            request_id = self.options.rid
            is_local = branch_name is not None
        else:
            request_id = guess_existing_review_request_id(
                repository_info,
                self.options.repository_name,
                api_root,
                api_client,
                self.tool,
                self.cmd_args,
                guess_summary=False,
                guess_description=False,
                is_fuzzy_match_func=self._ask_review_request_match)

            if not request_id:
                raise CommandError('Could not determine the existing review '
                                   'request URL to land.')

            is_local = True

        if self.options.is_local is not None:
            is_local = self.options.is_local

        if self.options.destination_branch is not None:
            destination_branch = self.options.destination_branch
        elif 'LAND_DEST_BRANCH' in self.config:
            destination_branch = self.config['LAND_DEST_BRANCH']
        else:
            raise CommandError('Please specify a destination branch.')

        if branch_name is None:
            branch_name = self.tool.get_current_branch()

        if branch_name == destination_branch:
            raise CommandError('The local branch cannot be merged onto '
                               'itself. Try a different local branch or '
                               'destination branch.')

        if self.tool.has_pending_changes():
            raise CommandError('Working directory is not clean.')

        review_request = get_review_request(request_id, api_root)

        try:
            is_rr_approved = review_request.approved
            approval_failure = review_request.approval_failure
        except AttributeError:
            # The Review Board server is an old version (pre-2.0) that
            # doesn't support the `approved` field. Determining it manually.
            if review_request.ship_it_count == 0:
                is_rr_approved = False
                approval_failure = \
                    'The review request has not been marked "Ship It!"'
            else:
                is_rr_approved = True
        finally:
            if not is_rr_approved:
                raise CommandError(approval_failure)

        if not is_local:
            self.patch(request_id)

        review_commit_message = extract_commit_message(review_request)
        author = review_request.get_submitter()

        try:
            self.tool.merge(
                branch_name,
                destination_branch,
                review_commit_message,
                author,
                self.options.squash)
        except MergeError as e:
            raise CommandError(str(e))

        try:
            self.tool.push_upstream(destination_branch)
        except PushError as e:
            raise CommandError(str(e))

        print("Review request %s has landed on '%s'." %
              (request_id, destination_branch))

    def _ask_review_request_match(self, review_request):
        return confirm(
            "Land Review Request #%s: '%s'? "
            % (review_request.id,
               get_draft_or_current_value('summary', review_request)))