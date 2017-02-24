# -*- coding: utf-8 -*-
# *********************************************************************
# lewis - a library for creating hardware device simulators
# Copyright (C) 2016-2017 European Spallation Source ERIC
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
# *********************************************************************

"""
This module contains :class:`DeviceBase` as a base class for other device classes and
infrastructure that can import devices from a module (:class:`DeviceRegistry`). The latter also
produces factory-like objects that create device instances and interfaces based on setups
(:class:`DeviceBuilder`).
"""

import importlib

from lewis import __version__
from lewis.adapters import Adapter
from lewis.core.exceptions import LewisException
from lewis.core.utils import get_submodules, get_members, is_compatible_with_framework
from lewis.core.logging import has_log


@has_log
class DeviceBase(object):
    """
    This class is a common base for :class:`~lewis.devices.Device` and
    :class:`~lewis.devices.StateMachineDevice`. It is mainly used in the device
    discovery process.
    """


def is_device(obj):
    """
    Returns True if obj is a device type (derived from DeviceBase), but not defined in
    :mod:`lewis.core.devices` or :mod:`lewis.devices`.

    :param obj: Object to test.
    :return: True if obj is a device type.
    """
    return isinstance(obj, type) and issubclass(
        obj, DeviceBase) and obj.__module__ not in ('lewis.devices', 'lewis.core.devices')


def is_adapter(obj):
    """
    Returns True if obj is an interface (derived from Adapter), but not defined in
    :mod:`lewis.adapters`.

    :param obj: Object to test.
    :return: True if obj is an interface type.
    """
    return isinstance(obj, type) and issubclass(
        obj, Adapter) and not obj.__module__.startswith('lewis.adapters')


class DeviceBuilder(object):
    """
    This class takes a module object (for example imported via importlib.import_module or via the
    :class:`DeviceRegistry`) and inspects it so that it's possible to construct devices and
    interfaces.

    Before loading anything, version compatibility between the device module and the framework is
    verified. If the module contains a ``framework_version`` variable at the top level holding a
    version spec (for example ``framework_version='>=1.0.0'`` or
    ``framework_version='>=1.0.1,<1.0.3'``), this spec is checked against the current version of
    Lewis. If the device module is incompatible, a
    :class:`~lewis.core.exceptions.VersionMismatchException` is raised. If the
    ``framework_version`` variable is missing, the device module will appear to be working with any
    version of the framework, but best practice is to specify a version number to avoid users from
    being faced with odd error messages or non-functioning devices.

    In order for the class to work properly, the device module has to adhere to a few rules.
    Device types, which means classes inheriting from :class:`DeviceBase`, are imported directly
    from the device module, equivalent to the following:

    .. sourcecode :: Python

        from device_name import SimulatedDeviceType

    If ``SimulatedDeviceType`` is defined in the ``__init__.py``, there's nothing else to do. If
    the device class is defined elsewhere, it must be imported in the ``__init__.py`` file as
    written above. If there is only one device type (which is probably the most common case), it is
    assumed to be default device type.

    Setups are discovered in two locations, the first one is a dict called ``setups`` in the device
    module, which must contain setup names as keys and as values again a dict. This inner dict has
    one mandatory key called ``device_type`` and one optional key ``parameters`` containing the
    constructor arguments for the specified device type:

    .. sourcecode:: Python

        setups = dict(
            broken=dict(
                device_type=SimulatedDeviceType,
                parameters=dict(
                    override_initial_state='error',
                    override_initial_data=dict(
                        target=-10, position=-20.0))))

    The other location is a sub-package called `setups`, which should in turn contain modules. Each
    module must contain a variable ``device_type`` and a variable ``parameters`` which are
    analogous to the keys in the dict described above. This allows for more complex setups which
    define additional classes and so on.

    The ``default`` setup is special, it is used when no setup is supplied to
    :meth:`create_device`. If the setup ``default`` is not defined, one is created with the default
    device type. This has two consequences, no setups need to be defined for very simple devices,
    but if multiple device types are defined, a ``default`` setup must be defined.

    A setup can be supplied to the :meth:`create_device`.

    Lastly, the builder tries to discover device interfaces, which are currently classes based on
    :class:`lewis.adapters.Adapter`. These are looked for in the module and in a sub-package
    called ``interfaces`` (which should contain modules with adapters like the ``setups`` package).

    Each interface has a protocol, if a protocol occurs more than once in a device module,
    a RuntimeError is raised.
    """

    def __init__(self, module):
        self._module = module

        self._device_types = list(get_members(self._module, is_device).values())

        if not self._device_types:
            # syslog.warning('No devices discovered in module {}.'.format(module.__name__))
            pass
        else:
            pass
            # syslog.info('Discovered devices in module {}: {}'.format(
            #    module.__name__, ','.join([x.__name__ for x in self._device_types])))

        submodules = get_submodules(self._module)

        self._setups = self._discover_setups(submodules.get('setups'))
        self._interfaces = self._discover_interfaces(submodules.get('interfaces'))

    def _discover_setups(self, setups_module):
        setups = getattr(self._module, 'setups', {})

        all_setups = setups if isinstance(setups, dict) else {}

        if setups_module is not None:
            for name, setup_module in get_submodules(setups_module).items():
                existing_setup = all_setups.get(name)

                if existing_setup is not None:
                    raise RuntimeError(
                        'The setup \'{}\' is defined twice in device \'{}\'.'.format(
                            existing_setup, self.name))

                all_setups[name] = {
                    'device_type': getattr(setup_module, 'device_type', self.default_device_type),
                    'parameters': getattr(setup_module, 'parameters', {})
                }

        if 'default' not in all_setups:
            all_setups['default'] = {'device_type': self.default_device_type}

        return all_setups

    def _discover_interfaces(self, interfaces_module):
        all_interfaces = []

        if interfaces_module is not None:
            for interface_module in get_submodules(interfaces_module).values():
                all_interfaces += list(get_members(interface_module, is_adapter).values())

        all_interfaces += list(get_members(self._module, is_adapter).values())

        interfaces = {}
        for interface in all_interfaces:
            existing_interface = interfaces.get(interface.protocol)

            if existing_interface is not None:
                raise RuntimeError(
                    'The protocol \'{}\' is defined in two interfaces for device \'{}\':\n'
                    '    {} (in {})\n'
                    '    {} (in {})\n'
                    'One of the protocol names needs to be changed.'.format(
                        interface.protocol, self.name, existing_interface.__name__,
                        existing_interface.__module__, interface.__name__, interface.__module__))

            interfaces[interface.protocol] = interface

        return interfaces

    @property
    def framework_version(self):
        return getattr(self._module, 'framework_version', None)

    @property
    def name(self):
        """
        The name of the device, which is also the name of the device module.
        """
        return self._module.__name__.split('.')[-1]

    @property
    def device_types(self):
        """
        This property contains a dict of all device types in the device module. The keys are
        type names, the values are the types themselves.
        """
        return self._device_types

    @property
    def default_device_type(self):
        """
        If the module only defines one device type, it is the default device type. It is used
        whenever a setup does not provide a ``device_type``.
        """
        if len(self.device_types) == 1:
            return self.device_types[0]

        return None

    @property
    def interfaces(self):
        """
        This property contains a map with protocols as keys and interface types as values.
        The types are imported from the ``interfaces`` sub-module and from the device module
        itself. If two interfaces with the same protocol are discovered, a RuntimeError is raiesed.
        """
        return self._interfaces

    @property
    def protocols(self):
        """All available protocols for this device."""
        return list(self.interfaces.keys())

    @property
    def default_protocol(self):
        """In case only one protocol exists for the device, this is the default protocol."""
        if len(self.protocols) == 1:
            return self.protocols[0]

        return None

    @property
    def setups(self):
        """
        A map with all available setups. Setups are imported from the ``setups`` dictionary
        in a device module and from the ``setups`` sub-module. If no ``default``-setup exists,
        one is created using the default_device_type. If there are several device types in
        the module, the default setup must be provided explicitly.
        """
        return self._setups

    def _create_device_instance(self, device_type, **kwargs):
        if device_type not in self.device_types:
            raise RuntimeError('Can not create instance of non-device type.')

        return device_type(**kwargs)

    def create_device(self, setup=None):
        """
        Creates a device object according to the provided setup. If no setup is provided,
        the default setup is used. If the setup can't be found, a LewisException is raised.
        This can also happen if the device type specified in the setup is invalid.

        :param setup: Name of the setup from which to create device.
        :return: Device object initialized according to the provided setup.
        """
        setup_name = setup if setup is not None else 'default'

        if setup_name not in self.setups:
            raise LewisException(
                'Failed to find setup \'{}\' for device \'{}\'. '
                'Available setups are:\n    {}'.format(
                    setup, self.name, '\n    '.join(self.setups.keys())))

        setup_data = self.setups[setup_name]
        device_type = setup_data.get('device_type') or self.default_device_type

        try:
            return self._create_device_instance(
                device_type, **setup_data.get('parameters', {}))
        except RuntimeError:
            raise LewisException(
                'The setup \'{}\' you tried to load does not specify a valid device type, but the '
                'device module \'{}\' provides multiple device types so that no meaningful '
                'default can be deduced.'.format(setup_name, self.name))

    def create_interface(self, protocol=None, *args, **kwargs):
        """
        Returns an interface that implements the provided protocol. If the protocol is not
        known, a LewisException is raised. All additional arguments are forwarded
        to the interface constructor (see :class:`~lewis.adapters.Adapter` for details).

        :param protocol: Protocol which the interface must implement.
        :param args: Positional arguments that are passed on to the interface.
        :param kwargs: Keyword arguments that are passed on to the interface.
        :return: Instance of the interface type.
        """
        protocol = protocol if protocol is not None else self.default_protocol

        try:
            return self.interfaces[protocol](*args, **kwargs)
        except KeyError:
            raise LewisException(
                '\'{}\' is not a valid protocol for device \'{}\', select one via the -p option.\n'
                'Available protocols are: \n    {}'.format(
                    protocol, self.name, '\n    '.join(self.interfaces.keys())))


@has_log
class DeviceRegistry(object):
    """
    This class takes the name of a module and constructs a :class:`DeviceBuilder` from
    each sub-module. The available devices can be queried and a DeviceBuilder can be
    obtained for each device:

    .. sourcecode:: Python

        from lewis.core.devices import DeviceRegistry

        registry = DeviceRegistry('lewis.devices')
        chopper_builder = registry.device_builder('chopper')

        # construct device, interface, ...

    If the module can not be imported, a LewisException is raised.

    :param device_module: Name of device module from which devices are loaded.
    """

    def __init__(self, device_module):
        try:
            self._device_module = importlib.import_module(device_module)
        except ImportError:
            raise LewisException(
                'Failed to import module \'{}\' for device discovery. '
                'Make sure that it is in the PYTHONPATH.\n'
                'See also the -a option of lewis.'.format(device_module))

        self._devices = {name: DeviceBuilder(module) for name, module in
                         get_submodules(self._device_module).items()}

    @property
    def devices(self):
        """All available device names."""
        return self._devices.keys()

    def device_builder(self, name, relaxed_versions=False):
        """
        Returns a :class:`DeviceBuilder` instance that can be used to create device objects
        based on setups, as well as device interfaces. If the device name is not stored
        in the internal map, a LewisException is raised.

        Each DeviceBuilder has a ``framework_version``-member, which specifies the version
        of Lewis the device has been written for. If the version does not match the current
        framework version, it is only possible to obtain those device builders calling the
        method with ``strict_versions`` set to ``False``, otherwise a
        :class:`~lewis.core.exceptions.LewisException` is raised. A warning message is logged
        in all cases.

        :param name: Name of the device.
        :param relaxed_versions: If ``False``, raise an exception when version of device does
                                not match framework version.
        :return: :class:`DeviceBuilder`-object for requested device.
        """
        try:
            builder = self._devices[name]

            if not is_compatible_with_framework(builder.framework_version):
                self.log.warn(
                    'Device \'%s\' is specified for a different framework version '
                    '(required: %s, current: %s). This means that the device might not work '
                    'as expected. Contact the device author about updating the device or use a '
                    'different version of lewis to run this device.',
                    builder.name, builder.framework_version, __version__)

                if not relaxed_versions:
                    raise LewisException(
                        'Not loading device \'{}\' with different framework version '
                        '(required: {}, current: {}) in strict mode. Use the --relaxed-versions '
                        'option of lewis to load the device anyway.'.format(
                            builder.name, builder.framework_version, __version__))

            return builder

        except KeyError:
            raise LewisException(
                'No device with the name \'{}\' could be found. '
                'Possible names are:\n    {}\n'
                'See also the -k option to add inspect a different module.'.format(
                    name, '\n    '.join(self.devices)))
