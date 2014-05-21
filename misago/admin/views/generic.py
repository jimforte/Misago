from django.contrib import messages
from django.core.paginator import Paginator, EmptyPage
from django.shortcuts import redirect
from django.utils.translation import ugettext_lazy as _
from django.views.generic import View
from misago.core.exceptions import ExplicitFirstPage
from misago.admin import site
from misago.admin.views import render


class AdminBaseMixin(object):
    """
    Admin mixin abstraciton used for configuring admin CRUD views.

    Takes following attributes:

    Model = Model instance
    message_404 = string used in "requested item not found" messages
    root_link = name of link leading to root action (eg. list of all items
    template_dir = directory with templates
    """
    Model = None
    message_404 = None
    root_link = None
    template_dir = None

    def get_model(self):
        return self.Model


class AdminView(View):
    def final_template(self):
        return '%s/%s' % (self.template_dir, self.template)

    def get_target(self, target):
        Model = self.get_model()
        return Model.objects.get(id=target)

    def _get_target(self, request, kwargs):
        """
        get_target is called by view to fetch item from DB
        """
        Model = self.get_model()

        try:
            return self.get_target(target)
        except Model.DoesNotExist:
            messages.error(request, self.message_404)
            return redirect(self.root_link)

    def current_link(self, request):
        matched_url = request.resolver_match.url_name
        return '%s:%s' % (request.resolver_match.namespace, matched_url)

    def process_context(self, request, context):
        return context

    def render(self, request, context=None):
        context = context or {}

        context['root_link'] = self.root_link
        context['current_link'] = self.current_link(request)

        self.process_context(request, context)

        return render(request, self.final_template(), context)


class ListView(AdminView):
    """
    Admin items list view

    Uses following attributes:

    template = template name used to render items list
    items_per_page = number of items displayed on single page
                     (enter 0 or don't define for no pagination)
    ordering = tuple of tuples defining allowed orderings
               typles should follow this format: (name, order_by)
    """
    template = 'list.html'

    items_per_page = 0
    ordering = None

    def get_queryset(self):
        return self.get_model().objects.all()

    def paginate_items(self, context, page):
        try:
            page = int(page)
            if page == 1:
                raise ExplicitFirstPage()
            elif page == 0:
                page = 1
        except ValueError:
            page_no = 1

        context['paginator'] = Paginator(context['items'],
                                         self.items_per_page,
                                         allow_empty_first_page=True)
        context['page'] = context['paginator'].page(page)
        context['items'] = context['page'].object_list

    def set_filters(self, request):
        pass

    def filter_items(self, request, context):
        context['is_filtering'] = False

    def ordering_session_key(self):
        return 'misago_admin_%s_order_by' % self.root_link

    def set_ordering(self, request, new_order):
        for order in self.ordering:
            if order[1] == new_order:
                request.session[self.ordering_session_key()] = order[1]
                return redirect(self.current_link(request))
        else:
            messages.error(request, _("New sorting method is incorrect."))
            raise ValueError()

    def order_items(self, request, context):
        current_ordering = request.session.get(self.ordering_session_key())

        for order in self.ordering:
            order_as_dict = {
                'name': order[0],
                'order_by': order[1],
                'type': 'desc' if order[1][0] == '-' else 'asc',
            }

            if order[1] == current_ordering:
                context['order'] = order_as_dict
                context['items'] = context['items'].order_by(
                    order_as_dict['order_by'])
            else:
                context['order_by'].append(order_as_dict)

        if not context['order']:
            current_ordering = context['order_by'].pop(0)
            context['order'] = current_ordering
            context['items'] = context['items'].order_by(
                current_ordering['order_by'])

    def dispatch(self, request, *args, **kwargs):
        context = {
            'items': self.get_queryset(),
            'paginator': None,
            'page': None,
            'order_by': [],
            'order': None,
        }

        if self.ordering:
            if request.method == 'POST' and 'order_by' in request.POST:
                try:
                    return self.set_ordering(request,
                                             request.POST.get('order_by'))
                except ValueError:
                    pass
            self.order_items(request, context)

        if self.items_per_page:
            self.paginate_items(context, kwargs.get('page', 0))

        return self.render(request, context)


class TargetedView(AdminView):
    def check_permissions(self, request, target=None):
        pass

    def get_target(self, kwargs):
        if len(kwargs):
            return self.get_model().objects.get(pk=kwargs[kwargs.keys()[0]])
        else:
            return self.get_model()()

    def get_target_or_none(self, request, kwargs):
        try:
            return self.get_target(kwargs)
        except self.get_model().DoesNotExist:
            return None

    def dispatch(self, request, *args, **kwargs):
        target = self.get_target_or_none(request, kwargs)
        if not target:
            messages.error(request, self.message_404)
            return redirect(self.root_link)

        error = self.check_permissions(request, target)
        if error:
            messages.error(request, error)
            return redirect(self.root_link)

        return self.real_dispatch(request, target)

    def real_dispatch(self, request, target=None):
        pass


class FormView(TargetedView):
    form = None
    template = 'form.html'
    message_submit = None

    def create_form(self, request, target=None):
        return self.form

    def initialize_form(self, FormType, request, target=None):
        if request.method == 'POST':
            return self.form(request.POST, request.FILES, instance=target)
        else:
            return self.form(instance=target)

    def handle_form(self, form, request):
        form.instance.save()
        if self.message_submit:
            message = self.message_submit % unicode(form.instance)
            messages.success(request, message)

    def real_dispatch(self, request, target=None):
        FormType = self.create_form(request, target)
        form = self.initialize_form(FormType, request, target)

        if form.is_valid():
            self.handle_form(form, request)

            if 'stay' in request.POST:
                return redirect(request.path)
            else:
                return redirect(self.root_link)

        return self.render(request, {'form': form, 'target': target})


class ButtonView(TargetedView):
    def real_dispatch(self, request, target=None):
        if request.method == 'POST':
            new_response = self.button_action(request, target)
            if new_response:
                return new_response
        return redirect(self.root_link)

    def button_action(self, request, target=None):
        raise NotImplementedError("You have to define custom button_action.")