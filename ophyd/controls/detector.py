# vi: ts=4 sw=4
'''
:mod:`ophyd.control.detector` - Ophyd Detectors Class
=====================================================

.. module:: ophyd.control.detector
   :synopsis:
'''

from __future__ import print_function
from .signal import (Signal, SignalGroup, EpicsSignal)


class DetectorStatus(object):
    def __init__(self, detector):
        self.done = False
        self.detector = detector

    def _finished(self, success=True, **kwargs):
        self.done = True


class Detector(SignalGroup):
    '''A Base Detector class

    Subclass from this to implement your own detectors
    '''


    def __init__(self, *args, **kwargs):
        super(Detector, self).__init__(*args, **kwargs)

    def configure(self, *args, **kwargs):
        '''Configure the detector for data collection.

        This method configures the Detector for data collection and is called
        before data collection starts.
        '''
        pass

    def deconfigure(self):
        '''Unset configuration of Detector

        This method resets the Detector and is called after data collection
        has stopped.
        '''
        pass

    def acquire(self, **kwargs):
        '''Start an acquisition on the detector (c.f. Trigger)

        This routine starts a data acquisition and returns an object which is
        the status of the acquisition.

        Returns
        -------
        DetectorStatus : Object to tell if detector has finished acquiring
        '''
        status = DetectorStatus(self)
        status.done = True
        return status

    #def read(self, **kwargs):
    #    '''Retrieve data from instrumentation, format it, and return it.
    #    '''
    #    raise NotImplementedError('Detector.read must be implemented')

    #def source(self, **kwargs):
    #    '''Get source info for a given detector'''
    #    raise NotImplementedError('Detector.source must be implemented')


class SignalDetector(Detector):
    def __init__(self, signal=None, *args, **kwargs):
        super(SignalDetector, self).__init__(*args, **kwargs)
        if signal is not None:
            if isinstance(signal, SignalGroup):
                [self.add_signal(sig) for sig in signal.signals]
            elif isinstance(signal, Signal):
                self.add_signal(signal)
            else:
                raise ValueError('signal must be Signal or SignalGroup instance')

