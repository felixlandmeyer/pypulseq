from types import SimpleNamespace
from typing import Optional, Tuple, List
from math import isclose, floor, ceil, lcm ,gcd
import itertools
import numpy as np
from pypulseq.opts import Opts


def make_adc(
    num_samples: int,
    delay: float = 0,
    duration: float = 0,
    dwell: float = 0,
    freq_offset: float = 0,
    phase_offset: float = 0,
    system: Opts = None,
) -> SimpleNamespace:
    """
    Create an ADC readout event.

    Parameters
    ----------
    num_samples: int
        Number of readout samples.
    system : Opts, default=Opts()
        System limits. Default is a system limits object initialised to default values.
    dwell : float, default=0
        ADC dead time in seconds (s) after sampling.
    duration : float, default=0
        Duration in seconds (s) of ADC readout event with `num_samples` number of samples.
    delay : float, default=0
        Delay in seconds (s) of ADC readout event.
    freq_offset : float, default=0
        Frequency offset of ADC readout event.
    phase_offset : float, default=0
        Phase offset of ADC readout event.

    Returns
    -------
    adc : SimpleNamespace
        ADC readout event.

    Raises
    ------
    ValueError
        If neither `dwell` nor `duration` are defined.
    """
    if system == None:
        system = Opts.default
        
    adc = SimpleNamespace()
    adc.type = "adc"
    adc.num_samples = num_samples
    adc.dwell = dwell
    adc.delay = delay
    adc.freq_offset = freq_offset
    adc.phase_offset = phase_offset
    adc.dead_time = system.adc_dead_time

    if (dwell == 0 and duration == 0) or (dwell > 0 and duration > 0):
        raise ValueError("Either dwell or duration must be defined")

    if duration > 0:
        adc.dwell = duration / num_samples

    if dwell > 0:
        adc.duration = dwell * num_samples

    if adc.dead_time > adc.delay:
        adc.delay = adc.dead_time

    return adc

def calc_adc_segments(
        num_samples: int,
        dwell: float,
        system: Optional[Opts] = None,
        mode: str = 'lengthen'
) -> Tuple[int, int]:
    """Calculate splitting of the ADC in segments with equal samples.

    Parameters
    ----------
    num_samples : int
        Initial number of samples to split into segments.
    dwell : float
        Dwell time of the ADC in [s]
    system : Optional[Opts], default=None
       System limits. Default is a system limits object initialised to default values.
    mode : str, default='lengthen'
        The total number of samples can either be shortened or legthend to match the constraints.

    Returns
    -------
    (num_segments, num_samples_seg): int, int
        Number of segments and number of samples per segment. 

    Notes
    ----- 
    "On some scanners, notably Siemens, ADC objects that exceed a certain 
    sample length (8192 samples on Siemens) should be splittable to N 
    equal parts, each of which aligned to the gradient raster. Each 
    segment, however, needs to have the number of samples smaller than 
    system.' and divisible by system.adcSamplesDivisor to be
    executable on the scanner. The optional parameter mode can be either
    'shorten' or 'lengthen'." [Matlab implementation of 'calcAdcSeg'. https://github.com/pulseq]
    
    Raises
    ------
    ValueError
        If 'mode' is not 'shorthen' or 'lengthen'.
    ValueError
        If no suitable segmentation could be found.
    ValueError
        If number of segments exceeds 128.
    """
    # Define maximum number of segments for the ADC
    MAX_SEGMENTS = 128
    
    if mode not in ['shorten', 'lengthen']:
        raise ValueError(
            f"'mode' must be 'shorten' or 'lengthen' but is {mode}"
        )

    if system is None:
        system = Opts.default

    # Check if single adc is sufficient
    if system.adc_samples_limit <= 0:
        return 1, num_samples
    
    # Get minimum number of samples for which the adc duration 
    # is a multiple of grad raster time (GRT) and adc raster time (ART)
    i_gr = round(system.grad_raster_time/system.adc_raster_time)  # GRT in units of ART
    if not isclose(system.grad_raster_time/system.adc_raster_time, i_gr):
        raise ValueError(
            "System 'grad_raster_time' is not a multiple of 'adc_raster_time'.")

    i_dwell = round(dwell/system.adc_raster_time)  # dwell in units of ART
    if not isclose(dwell/system.adc_raster_time, i_dwell):
        raise ValueError(
            "'dwell' is not a multiple of system 'adc_raster_time'.")

    i_common = lcm(i_gr, i_dwell)
    min_samples_segment = int(i_common/i_dwell)  # lcm(a,b)/b is always int

    # Siemens: Number of Samples should be divisible by a divisor
    gcd_adcdiv = gcd(min_samples_segment, system.adc_samples_divisor)
    if gcd_adcdiv != system.adc_samples_divisor:
        min_samples_segment *= system.adc_samples_divisor/gcd_adcdiv
    
    # Get segment multiplier
    if mode == 'shorten':
        samples_seg_multip = floor(num_samples/min_samples_segment)
    else:
        samples_seg_multip = ceil(num_samples/min_samples_segment)
    while (samples_seg_multip > 0
                and samples_seg_multip < (2*num_samples/min_samples_segment)):
        # Get prime factors from segments multiplier
        adc_seg_primes = _prime_factors(samples_seg_multip)
        num_segments = 1
        if len(adc_seg_primes) > 1:
            prime_permutations = list(
                itertools.permutations(adc_seg_primes))
            # Get candidates for samples in single segment
            num_segments_candids = np.unique(
                np.cumprod(prime_permutations, axis=1)
            )
            # Find suitable candidate
            for candid in num_segments_candids:
                num_segments = candid
                num_samples_seg = (
                    samples_seg_multip 
                    * min_samples_segment 
                    / num_segments
                )
                if (num_samples_seg <= system.adc_samples_limit
                        and num_segments<=MAX_SEGMENTS):
                    break  # Found segments and samples
        else:  # Only one pollible solution
            num_samples_seg = samples_seg_multip * min_samples_segment
        
        # Does output already fullfills contraints?
        if (num_samples_seg <= system.adc_samples_limit
                and num_segments<=MAX_SEGMENTS):
            break  
        else: # Shorten or lengthen the number of samples per segment
            samples_seg_multip += (1 if mode=='lengthen' else -1)
            
    # Validate contraints
    if samples_seg_multip <= 0:
        raise ValueError(
            "Could not find suitable segmentation.")
    if num_samples_seg == 0:
        raise ValueError(
            "Could not find suitable number of samples per segment.")
    if num_segments > MAX_SEGMENTS:
        raise ValueError(
            f"Number of segments ({num_segments}) exceeds allowed number of {MAX_SEGMENTS}")
        
    return num_segments, num_samples_seg
    


def _prime_factors(n) -> List[int]:
    if n == 1:
        return [1]
    else:
        factors = []
        # Dividing n by 2 until it's odd
        while n % 2 == 0:
            factors.append(2)
            n //= 2
        
        # Checking odd factors from 3 upwards
        for i in range(3, int(n**0.5) + 1, 2):
            while n % i == 0:
                factors.append(i)
                n //= i
                
        if n > 2:
            factors.append(n)
        
        return factors