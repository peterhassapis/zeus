import csv
import json

from django.conf.urls.defaults import *
from django.core.urlresolvers import reverse
from django.forms import ValidationError
from django.utils.translation import ugettext_lazy as _
from django.db import connection

from zeus.forms import ElectionForm
from zeus import auth
from zeus.forms import PollForm, PollFormSet, EmailVotersForm
from zeus.utils import *
from zeus.views.utils import *
from zeus import tasks

from django.utils.encoding import smart_unicode
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.http import HttpResponseRedirect, HttpResponse
from django.core.exceptions import PermissionDenied
from django.forms.models import modelformset_factory
from django.template.loader import render_to_string
from django.contrib import messages

from helios.view_utils import render_template
from helios.models import Election, Poll, Voter, VoterFile, CastVote, \
    AuditedBallot
from helios import datatypes
from helios.crypto import utils as crypto_utils
from helios.crypto import electionalgs
from helios.utils import force_utf8


@auth.election_admin_required
def list(request, election):
    polls = election.polls.filter()
    context = {'polls': polls, 'election': election}
    set_menu('polls', context)
    return render_template(request, "election_polls_list", context)


@auth.election_admin_required
@auth.requires_election_features('can_manage_polls')
def manage(request, election, poll=None):
    polls = election.polls.filter()
    extra = int(request.GET.get('extra', 2))
    polls_formset = modelformset_factory(Poll, PollForm, extra=extra,
                                         max_num=100, formset=PollFormSet,
                                         can_delete=False)
    if request.method == "GET":
        form = polls_formset(queryset=election.polls.filter())
    else:
        form = polls_formset(request.POST,
                             queryset=election.polls.filter())
        if form.is_valid():
            with transaction.commit_on_success():
                new_poll = form.save(election)
                url = election_reverse(election, 'polls_list')
                return HttpResponseRedirect(url)

    context = {
        'election': election,
        'polls': polls,
        'form': form
    }
    return render_template(request, "election_polls_manage", context)


@auth.election_admin_required
def remove(request, election, poll):
    # TODO: fix this
    if request.method != 'POST':
        raise PermissionDenied
    poll.delete()
    return HttpResponseRedirect(election_reverse(election, 'polls_list'))


@auth.election_admin_required
@auth.requires_poll_features('can_manage_questions')
def questions_manage(request, election, poll):
    module = poll.get_module()
    return module.questions_update_view(request, election, poll)


@auth.election_view()
def questions(request, election, poll):
    module = poll.get_module()
    if request.zeususer.is_admin:
        if not module.questions_set() and poll.feature_can_manage_questions:
            url = poll_reverse(poll, 'questions_manage')
            return HttpResponseRedirect(url)

    context = {
        'election': election,
        'poll': poll,
        'questions': questions,
        'module': poll.get_module()
    }
    set_menu('questions', context)
    tpl = getattr(module, 'questions_list_template', 'election_poll_questions')
    return render_template(request, tpl, context)


@auth.election_admin_required
def voters_list(request, election, poll):
    # for django pagination support
    page = int(request.GET.get('page', 1))
    limit = int(request.GET.get('limit', 10))
    q = request.GET.get('q','')
    voters_per_page = getattr(settings, 'ELECTION_VOTERS_PER_PAGE', 100)
    order_by = request.GET.get('order', 'surname')
    if not order_by in ['surname', 'email', 'name']:
        order_by = 'surname'

    validate_hash = request.GET.get('vote_hash', "").strip()
    hash_invalid = None
    hash_valid = None

    voters = Voter.objects.filter(poll=poll).order_by('voter_%s' % order_by)

    if q != '':
        q = Q()
        for search_field in ['name', 'surname', 'email']:
            kwargs = {'voter__%s__icontains' % search_field: q}
            q = q | Q(**kwargs)
        voters = voters.filter(q)

    voters_count = Voter.objects.filter(poll=poll).count()
    voted_count = poll.voters_cast_count()

    context = {
        'election': election,
        'poll': poll,
        'limit': limit,
        'page': page,
        'voters': voters,
        'voters_count': voters_count,
        'voted_count': voted_count,
        'q': q,
        'voters_per_page': voters_per_page
    }
    set_menu('voters', context)
    return render_template(request, 'election_poll_voters_list', context)


@auth.election_admin_required
@auth.requires_poll_features('can_clear_voters')
@transaction.commit_on_success
def voters_clear(request, election, poll):
    for voter in poll.voters.all():
        if not voter.cast_votes.count():
            voter.delete()
    url = poll_reverse(poll, 'voters')
    return HttpResponseRedirect(url)


@auth.election_admin_required
@auth.requires_poll_features('can_add_voter')
def voters_upload(request, election, poll):
    common_context = {
        'election': election,
        'poll': poll
    }
    set_menu('voters', common_context)
    if request.method == "POST":
        if bool(request.POST.get('confirm_p', 0)):
            # launch the background task to parse that file
            voter_file_id = request.session.get('voter_file_id', None)
            if not voter_file_id:
                raise Exception("Invalid voter file id")
            try:
                voter_file = VoterFile.objects.get(pk=voter_file_id)
                voter_file.process()
            except VoterFile.DoesNotExist:
                pass
            except KeyError:
                pass
            if 'voter_file_id' in request.session:
                del request.session['voter_file_id']
            url = poll_reverse(poll, 'voters')
            return HttpResponseRedirect(url)
        else:
            # we need to confirm
            error = None
            if request.FILES.has_key('voters_file'):
                voters_file = request.FILES['voters_file']
                voter_file_obj = poll.add_voters_file(voters_file)
            # import the first few lines to check
            voters = []
            try:
                voters = [v for v in voter_file_obj.itervoters()]
            except ValidationError, e:
                if hasattr(e, 'messages') and e.messages:
                    error = e.messages[0]
                else:
                    error = str(e)
            except Exception, e:
                error = str(e)
            if not error:
                request.session['voter_file_id'] = voter_file_obj.id
            count = len(voters)
            context = common_context
            context.update({
                'voters': voters,
                'count': count,
                'error': error
            })
            return render_template(request,
                                   'election_poll_voters_upload_confirm',
                                   context)
    else:
        if 'voter_file_id' in request.session:
            del request.session['voter_file_id']
        return render_template(request,
                               'election_poll_voters_upload',
                               common_context)


@auth.election_admin_required
def voters_upload_cancel(request, election, poll):
    voter_file_id = request.session.get('voter_file_id', None)
    if voter_file_id:
        vf = VoterFile.objects.get(id = voter_file_id)
        vf.delete()
    if 'voter_file_id' in request.session:
        del request.session['voter_file_id']

    url = poll_reverse(poll, 'voters_upload')
    return HttpResponseRedirect(url)


@auth.election_admin_required
@auth.requires_poll_features('can_send_voter_mail')
def voters_email(request, election, poll, voter_uuid=None):
    user = request.admin

    TEMPLATES = [
        ('vote', _('Time to Vote')),
        ('info', _('Additional Info'))
    ]

    default_template = 'vote'
    if not poll.check_feature('can_send_voter_booth_invitation'):
        TEMPLATES.pop(0)
        default_template = 'info'

    template = request.REQUEST.get('template', default_template)

    if not template in [t[0] for t in TEMPLATES]:
        raise Exception("bad template")

    voter = None
    if voter_uuid:
        try:
            voter = poll.voters.get(uuid=voter_uuid)
        except Voter.DoesNotExist:
            raise PermissionDenied
        if not voter:
            url = election_reverse(election, 'index')
            return HttpResponseRedirect(url)

    election_url = election.get_absolute_url()

    default_subject = render_to_string(
        'email/%s_subject.txt' % template, {
            'custom_subject': "&lt;SUBJECT&gt;"
        })

    default_body = render_to_string(
        'email/%s_body.txt' % template, {
            'election' : election,
            'election_url' : election_url,
            'custom_subject' : default_subject,
            'custom_message': '&lt;BODY&gt;',
            'voter': {
                'vote_hash' : '<SMART_TRACKER>',
                'name': '<VOTER_NAME>',
                'voter_name': '<VOTER_NAME>',
                'voter_surname': '<VOTER_SURNAME>',
                'voter_login_id': '<VOTER_LOGIN_ID>',
                'voter_password': '<VOTER_PASSWORD>',
                'audit_passwords': '1',
                'get_audit_passwords': ['pass1', 'pass2', '...'],
                'get_quick_login_url': '<VOTER_LOGIN_URL>',
                'voter_type': poll.voters.all()[0].voter_type,
                'poll': poll,
                'election' : election}
            })

    if request.method == "GET":
        email_form = EmailVotersForm()
        email_form.fields['subject'].initial = dict(TEMPLATES)[template]
        if voter:
            email_form.fields['send_to'].widget = \
                email_form.fields['send_to'].hidden_widget()
    else:
        email_form = EmailVotersForm(request.POST)
        if email_form.is_valid():
            # the client knows to submit only once with a specific voter_id
            subject_template = 'email/%s_subject.txt' % template
            body_template = 'email/%s_body.txt' % template
            extra_vars = {
                'custom_subject' : email_form.cleaned_data['subject'],
                'custom_message' : email_form.cleaned_data['body'],
                'election_url' : election_url,
                'election' : election,
            }
            voter_constraints_include = None
            voter_constraints_exclude = None
            update_booth_invitation_date = False
            if template == 'vote':
                update_booth_invitation_date = True

            if voter:
                tasks.single_voter_email.delay(voter_uuid=voter.uuid,
                                       subject_template=subject_template,
                                       body_template=body_template,
                                       extra_vars=extra_vars,
                                       update_date=True,
                                       update_booth_invitation_date=update_booth_invitation_date)
                url = poll_reverse(poll, 'voters')
                return HttpResponseRedirect(url)
            else:
                # exclude those who have not voted
                if email_form.cleaned_data['send_to'] == 'voted':
                    voter_constraints_exclude = {'vote_hash' : None}

                # include only those who have not voted
                if email_form.cleaned_data['send_to'] == 'not-voted':
                    voter_constraints_include = {'vote_hash': None}

                tasks.voters_email.delay(poll.pk,
                                     subject_template=subject_template,
                                     body_template=body_template,
                                     extra_vars=extra_vars,
                                     voter_constraints_include=voter_constraints_include,
                                     voter_constraints_exclude=voter_constraints_exclude,
                                     update_date=True,
                                     update_booth_invitation_date=update_booth_invitation_date)


                # this batch process is all async, so we can return a nice note
                messages.info(request, _("Email sending started"))
                url = poll_reverse(poll, 'voters')
                return HttpResponseRedirect(url)

    context = {
        'email_form': email_form,
        'election': election,
        'poll': poll,
        'voter_o': voter,
        'default_subject': default_subject,
        'default_body': default_body,
        'template': template,
        'templates': TEMPLATES
    }
    set_menu('voters', context)
    return render_template(request, "voters_email", context)


@auth.election_admin_required
@auth.requires_poll_features('delete_voter')
def voter_delete(request, election, poll, voter_uuid):
    voter = get_object_or_404(Voter, poll=poll, uuid=voter_uuid)
    if voter.voted:
        raise PermissionDenied
    voter.delete()
    url = poll_reverse(poll, 'voters')
    return HttpResponseRedirect(url)


@auth.election_admin_required
@auth.requires_poll_features('can_exclude_voter')
def voter_exclude(request, election, poll, voter_uuid):
    #TODO: ALLOW ONLY POST
    voter = get_object_or_404(Voter, uuid=voter_uuid, poll=poll)
    if not voter.excluded_at:
        reason = request.POST.get('reason', '')
        poll.zeus.exclude_voter(voter.uuid, reason)
    return HttpResponseRedirect(poll_reverse(poll, 'voters'))


@auth.election_admin_required
def voters_csv(request, election, poll, fname):
    response = HttpResponse(mimetype='text/csv')
    filename = smart_unicode("voters-%s.csv" % election.short_name)
    if fname:
        filename = fname
    response['Content-Dispotition'] = \
            'attachment; filename="%s.csv"' % filename
    poll.voters_to_csv(response)
    return response


@auth.election_view(check_access=False)
def voter_booth_login(request, election, poll, voter_uuid, voter_secret):
    voter = None
    try:
        voter = Voter.objects.get(poll=poll, uuid=voter_uuid)
    except Voter.DoesNotExist:
        raise PermissionDenied("Invalid election")

    if voter.voter_password == unicode(voter_secret):
        user = auth.ZeusUser(voter)
        user.authenticate(request)
        return HttpResponseRedirect(poll_reverse(poll, 'index'))
    raise PermissionDenied


@auth.election_view(check_access=False)
def to_json(request, election, poll):
    data = poll.get_booth_dict()
    return HttpResponse(json.dumps(data, default=common_json_handler),
                        mimetype="application/json")


@auth.poll_voter_required
@auth.requires_poll_features('can_cast_vote')
def post_audited_ballot(request, election, poll):
    # TODO CHECK REQUEST METHOD, AND CSRF CHECK
    voter = request.voter

    raw_vote = request.POST['audited_ballot']
    encrypted_vote = crypto_utils.from_json(raw_vote)
    audit_request = crypto_utils.from_json(request.session['audit_request'])
    audit_password = request.session['audit_password']

    if not audit_password:
        raise Exception("Auditing with no password")

    # fill in the answers and randomness
    audit_request['answers'][0]['randomness'] = \
            encrypted_vote['answers'][0]['randomness']
    audit_request['answers'][0]['answer'] = \
            [encrypted_vote['answers'][0]['answer'][0]]
    encrypted_vote = electionalgs.EncryptedVote.fromJSONDict(audit_request)

    del request.session['audit_request']
    del request.session['audit_password']

    poll.cast_vote(voter, encrypted_vote, audit_password)
    vote_pk = AuditedBallot.objects.filter(voter=voter).order_by('-pk')[0].pk

    return HttpResponse(json.dumps({'audit_id': vote_pk }),
                        content_type="application/json")


@auth.poll_voter_required
@auth.requires_poll_features('can_cast_vote')
def cast(request, election, poll):
    # TODO CHECK REQUEST METHOD, AND CSRF CHECK
    voter = request.voter

    encrypted_vote = request.POST['encrypted_vote']
    vote = datatypes.LDObject.fromDict(crypto_utils.from_json(encrypted_vote),
        type_hint='phoebus/EncryptedVote').wrapped_obj
    audit_password = request.POST.get('audit_password', None)

    cursor = connection.cursor()
    try:
        cursor.execute("SELECT pg_advisory_lock(1)")
        with transaction.commit_on_success():
            cast_result = poll.cast_vote(voter, vote, audit_password)
    finally:
        cursor.execute("SELECT pg_advisory_unlock(1)")

    signature = {'signature': cast_result}

    if 'audit_request' in request.session:
        del request.session['audit_request']

    if signature['signature'].startswith("AUDIT REQUEST"):
        request.session['audit_request'] = encrypted_vote
        request.session['audit_password'] = audit_password
        token = request.session.get('csrf_token')
        return HttpResponse('{"audit": 1, "token":"%s"}' % token,
                            mimetype="application/json")
    else:
        # notify user
        tasks.send_cast_vote_email.delay(election, voter, signature)
        fingerprint = voter.cast_votes.filter()[0].fingerprint
        url = "%s%s?f=%s" % (settings.SECURE_URL_HOST, poll_reverse(poll,
                                                               'cast_done'),
                             fingerprint)

        return HttpResponse('{"cast_url": "%s"}' % url,
                            mimetype="application/json")


@auth.election_view(check_access=False)
def cast_done(request, election, poll):
    if request.zeususer.is_authenticated() and request.zeususer.is_voter:
        request.zeususer.logout(request)

    fingerprint = request.GET.get('f')
    if not request.GET.get('f', None):
        raise PermissionDenied()

    vote = get_object_or_404(CastVote, fingerprint=fingerprint)

    return render_template(request, 'election_poll_cast_done', {
                               'cast_vote': vote,
                               'election_uuid': election.uuid,
                               'poll_uuid': poll.uuid
    })


@auth.election_view(check_access=False)
def download_signature(request, election, poll, fingerprint):
    vote = CastVote.objects.get(voter__poll=poll, fingerprint=fingerprint)
    response = HttpResponse(content_type='application/binary')
    response['Content-Dispotition'] = 'attachment; filename=signature.txt'
    response.write(vote.signature['signature'])
    return response


@auth.election_view()
def audited_ballots(request, election, poll):

    vote_hash = request.GET.get('vote_hash', None)
    if vote_hash:
        b = get_object_or_404(AuditedBallot, poll=poll, vote_hash=vote_hash)
        b = AuditedBallot.objects.get(poll=poll,
                                      vote_hash=request.GET['vote_hash'])
        return HttpResponse(b.raw_vote, mimetype="text/plain")

    audited_ballots = AuditedBallot.objects.filter(is_request=False,
                                                   poll=poll)

    voter = None
    if request.zeususer.is_voter:
        voter = request.voter

    voter_audited_ballots = []
    if voter:
        voter_audited_ballots = AuditedBallot.objects.filter(poll=poll,
                                                             is_request=False,
                                                             voter=voter)
    context = {
        'election': election,
        'audited_ballots': audited_ballots,
        'voter_audited_ballots': voter_audited_ballots,
        'poll': poll,
        'per_page': 50
    }
    set_menu('audited_ballots', context)
    return render_template(request, 'election_poll_audited_ballots', context)


@auth.trustee_view
@auth.requires_poll_features('can_do_partial_decrypt')
@transaction.commit_on_success
def upload_decryption(request, election, poll, trustee):
    factors_and_proofs = crypto_utils.from_json(
        request.POST['factors_and_proofs'])

    # verify the decryption factors
    LD = datatypes.LDObject
    factors_data = factors_and_proofs['decryption_factors']
    factor = lambda fd: LD.fromDict(fd, type_hint='core/BigInteger').wrapped_obj
    factors = [[factor(f) for f in factors_data[0]]]

    proofs_data = factors_and_proofs['decryption_proofs']
    proof = lambda pd: LD.fromDict(pd, type_hint='legacy/EGZKProof').wrapped_obj
    proofs = [[proof(p) for p in proofs_data[0]]]

    tasks.poll_add_trustee_factors.delay(poll.pk, trustee.pk, factors, proofs)

    return HttpResponse("SUCCESS")


@auth.election_view()
@auth.requires_poll_features('can_do_partial_decrypt')
def get_tally(request, election, poll):
    if not request.zeususer.is_trustee:
        raise PermissionDenied

    params = poll.get_booth_dict()
    tally = poll.encrypted_tally.toJSONDict()

    return HttpResponse(json.dumps({
        'poll': params,
        'tally': tally}, default=common_json_handler),
        mimetype="application/json")


@auth.election_view()
@auth.requires_poll_features('compute_results_finished')
def results(request, election, poll):
    context = {
        'poll': poll,
        'election': election
    }
    set_menu('results', context)
    return render_template(request, 'election_poll_results', context)