###############################################################################
#
# Copyright (C) 2017 Andrew Muzikin
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
#
###############################################################################

from logbook import Logger, StreamHandler, WARNING

import datetime
import random
from numpy.random import beta as random_beta
import copy
import os
import sys

import backtrader.feeds as btfeeds
import pandas as pd

DataSampleConfig = dict(
    get_new=True,
    sample_type=0,
    b_alpha=1,
    b_beta=1
)
"""
dict: Conventional sampling configuration template to pass to data class `sample()` method:

```sample = my_data.sample(**DataSampleConfig)```
"""


EnvResetConfig = dict(
    episode_config=copy.deepcopy(DataSampleConfig),
    trial_config=copy.deepcopy(DataSampleConfig),
)
"""
dict: Conventional reset configuration template to pass to environment `reset()` method:

```observation = env.reset(**EnvResetConfig)```
"""


class BTgymBaseData:
    """
    Base BTgym data class.
    Provides core data loading, sampling, splitting  and converting functionality.
    Do not use directly.

    Enables Pipe::

        CSV[source data]-->pandas[for efficient sampling]-->bt.feeds

    """

    def __init__(
            self,
            filename=None,
            parsing_params=None,
            sampling_params=None,
            name='base_data',
            task=0,
            log_level=WARNING,
            _config_stack=None,
            **kwargs
    ):
        """
        Args:

            filename:                       Str or list of str, should be given either here or when calling read_csv(),
                                            see `Notes`.

            specific_params CSV to Pandas parsing

            sep:                            ';'
            header:                         0
            index_col:                      0
            parse_dates:                    True
            names:                          ['open', 'high', 'low', 'close', 'volume']

            specific_params Pandas to BT.feeds conversion

            timeframe=1:                    1 minute.
            datetime:                       0
            open:                           1
            high:                           2
            low:                            3
            close:                          4
            volume:                         -1
            openinterest:                   -1

            specific_params Sampling

            sample_class_ref:               None - if not None, than sample() method will return instance of specified
                                            class, which itself must be subclass of BaseBTgymDataset,
                                            else returns instance of the base data class.

            start_weekdays:                 [0, 1, 2, 3, ] - Only weekdays from the list will be used for sample start.
            start_00:                       True - sample start time will be set to first record of the day
                                            (usually 00:00).
            sample_duration:                {'days': 1, 'hours': 23, 'minutes': 55} - Maximum sample time duration
                                            in days, hours, minutes
            time_gap:                       {''days': 0, hours': 5, 'minutes': 0} - Data omittance threshold:
                                            maximum no-data time gap allowed within sample in days, hours.
                                            Thereby, if set to be < 1 day, samples containing weekends and holidays gaps
                                            will be rejected.
            test_period:                    {'days': 0, 'hours': 0, 'minutes': 0} - setting this param to non-zero
                                            duration forces instance.data split to train / test subsets with test
                                            subset duration equal to `test_period` with `time_gap` tolerance. Train data
                                            always precedes test one:
                                            [0_record<-train_data->split_point_record<-test_data->last_record].
            sample_expanding:               None, reserved for child classes.

        Note:
            - CSV file can contain duplicate records, checks will be performed and all duplicates will be removed;

            - CSV file should be properly sorted by date_time in ascending order, no sorting checks performed.

            - When supplying list of file_names, all files should be also listed ascending by their time period,
              no correct sampling will be possible otherwise.

            - Default parameters are source-specific and made to correctly parse 1 minute Forex generic ASCII
              data files from www.HistData.com. Tune according to your data source.
        """
        self.filename = filename

        if parsing_params is None:
            self.parsing_params = dict(
                # Default parameters for source-specific CSV datafeed class,
                # correctly parses 1 minute Forex generic ASCII
                # data files from www.HistData.com:

                # CSV to Pandas params.
                sep=';',
                header=0,
                index_col=0,
                parse_dates=True,
                names=['open', 'high', 'low', 'close', 'volume'],

                # Pandas to BT.feeds params:
                timeframe=1,  # 1 minute.
                datetime=0,
                open=1,
                high=2,
                low=3,
                close=4,
                volume=-1,
                openinterest=-1,
            )
        else:
            self.parsing_params = parsing_params

        if sampling_params is None:
            self.sampling_params = dict(
                # Sampling params:
                start_weekdays=[],  # Only weekdays from the list will be used for episode start.
                start_00=False,  # Sample start time will be set to first record of the day (usually 00:00).
                sample_duration=dict(  # Maximum sample time duration in days, hours, minutes:
                    days=0,
                    hours=0,
                    minutes=0
                ),
                time_gap=dict(  # Maximum data time gap allowed within sample in days, hours. Thereby,
                    days=0,  # if set to be < 1 day, samples containing weekends and holidays gaps will be rejected.
                    hours=0,
                ),
                test_period=dict(  # Time period to take test samples from, in days, hours, minutes:
                    days=0,
                    hours=0,
                    minutes=0
                ),
                expanding=False,
            )
        else:
            self.sampling_params = sampling_params

        self.name = name
        self.task = task
        self.log_level = log_level

        self.data = None  # Will hold actual data as pandas dataframe
        self.is_ready = False
        self.data_stat = None  # Dataset descriptive statistic as pandas dataframe
        self.data_range_delta = None  # Dataset total duration timedelta
        self.max_time_gap = None
        self.time_gap = None
        self.max_sample_len_delta = None
        self.sample_duration = None
        self.sample_num_records = 0
        self.start_weekdays = None
        self.start_00 = None
        self.expanding = None

        self.sample_instance = None

        self.test_range_delta = None
        self.train_range_delta = None
        self.test_num_records = 0
        self.train_num_records = 0
        self.train_interval = [0, 0]
        self.test_interval = [0, 0]
        self.test_period = {'days': 0, 'hours': 0, 'minutes': 0}
        self.train_period = {'days': 0, 'hours': 0, 'minutes': 0}
        self.sample_num = 0
        self.task = 0
        self.metadata = {'sample_num': 0, 'type': None}

        self.set_params(self.parsing_params)
        self.set_params(self.sampling_params)

        self._config_stack = copy.deepcopy(_config_stack)
        try:
            nested_config = self._config_stack.pop()

        except (IndexError, AttributeError) as e:
            # IF stack is empty, sample of this instance itself is not supposed to be sampled.
            nested_config = dict(
                class_ref=None,
                kwargs=dict(
                    parsing_params=self.parsing_params,
                    sample_params=None,
                    name='data_stream',
                    task=self.task,
                    log_level=self.log_level,
                    _config_stack=None,
                )
            )
        # Configure sample instance parameters:
        self.nested_class_ref = nested_config['class_ref']
        self.nested_params = nested_config['kwargs']
        self.sample_name = '{}_w_{}_'.format(self.nested_params['name'], self.task)
        self.nested_params['_config_stack'] = self._config_stack

        # Logging:
        StreamHandler(sys.stdout).push_application()
        self.log = Logger('{}_{}'.format(self.name, self.task), level=self.log_level)

        # Legacy parameter dictionary, left here for BTgym API_shell:
        self.params = {}
        self.params.update(self.parsing_params)
        self.params.update(self.sampling_params)

    def set_params(self, params_dict):
        """
        Batch attribute setter.

        Args:
            params_dict: dictionary of parameters to be set as instance attributes.
        """
        for key, value in params_dict.items():
            setattr(self, key, value)

    def set_logger(self, level=None, task=None):
        """
        Sets logbook logger.

        Args:
            level:  logbook.level, int
            task:   task id, int

        """
        if task is not None:
            self.task = task

        if level is not None:
            self.log = Logger('{}_{}'.format(self.name, self.task), level=level)

    def reset(self, data_filename=None, **kwargs):
        """
        Gets instance ready.

        Args:
            data_filename:  [opt] string or list of strings.
            kwargs:         not used.

        """
        self._reset(data_filename=data_filename, **kwargs)

    def _reset(self, data_filename=None, **kwargs):

        self.read_csv(data_filename)

        # Maximum data time gap allowed within sample as pydatetimedelta obj:
        self.max_time_gap = datetime.timedelta(**self.time_gap)

        # ... maximum episode time duration:
        self.max_sample_len_delta = datetime.timedelta(**self.sample_duration)

        # Maximum possible number of data records (rows) within episode:
        self.sample_num_records = int(self.max_sample_len_delta.total_seconds() / (60 * self.timeframe))

        # Train/test timedeltas:
        self.test_range_delta = datetime.timedelta(**self.test_period)
        self.train_range_delta = datetime.timedelta(**self.sample_duration) - datetime.timedelta(**self.test_period)

        self.test_num_records = round(self.test_range_delta.total_seconds() / (60 * self.timeframe))
        self.train_num_records = self.data.shape[0] - self.test_num_records

        break_point = self.train_num_records

        try:
            assert self.train_num_records >= self.sample_num_records

        except AssertionError:
            self.log.exception(
                'Train subset should contain at least one sample, got: train_set size: {} rows, sample_size: {} rows'.
                format(self.train_num_records, self.sample_num_records)
            )
            raise AssertionError

        if self.test_num_records > 0:
            try:
                assert self.test_num_records >= self.sample_num_records

            except AssertionError:
                self.log.exception(
                    'Test subset should contain at least one sample, got: test_set size: {} rows, sample_size: {} rows'.
                    format(self.test_num_records, self.sample_num_records)
                )
                raise AssertionError

        self.train_interval = [0, break_point]
        self.test_interval = [break_point, self.data.shape[0]]

        self.sample_num = 0
        self.is_ready = True

    def read_csv(self, data_filename=None, force_reload=False):
        """
        Populates instance by loading data: CSV file --> pandas dataframe.

        Args:
            data_filename: [opt] csv data filename as string or list of such strings.
            force_reload:  ignore loaded data.
        """
        if self.data is not None and not force_reload:
            self.log.debug('data has been already loaded. Use `force_reload=True` to reload')
            return
        if data_filename:
            self.filename = data_filename  # override data source if one is given
        if type(self.filename) == str:
            self.filename = [self.filename]

        dataframes = []
        for filename in self.filename:
            try:
                assert filename and os.path.isfile(filename)
                current_dataframe = pd.read_csv(
                    filename,
                    sep=self.sep,
                    header=self.header,
                    index_col=self.index_col,
                    parse_dates=self.parse_dates,
                    names=self.names
                )

                # Check and remove duplicate datetime indexes:
                duplicates = current_dataframe.index.duplicated(keep='first')
                how_bad = duplicates.sum()
                if how_bad > 0:
                    current_dataframe = current_dataframe[~duplicates]
                    self.log.warning('Found {} duplicated date_time records in <{}>.\
                     Removed all but first occurrences.'.format(how_bad, filename))

                dataframes += [current_dataframe]
                self.log.info('Loaded {} records from <{}>.'.format(dataframes[-1].shape[0], filename))

            except:
                msg = 'Data file <{}> not specified / not found.'.format(str(filename))
                self.log.error(msg)
                raise FileNotFoundError(msg)

        self.data = pd.concat(dataframes)
        range = pd.to_datetime(self.data.index)
        self.data_range_delta = (range[-1] - range[0]).to_pytimedelta()

    def describe(self):
        """
        Returns summary dataset statistic as pandas dataframe:

            - records count,
            - data mean,
            - data std dev,
            - min value,
            - 25% percentile,
            - 50% percentile,
            - 75% percentile,
            - max value

        for every data column.
        """
        # Pretty straightforward, using standard pandas utility.
        # The only caveat here is that if actual data has not been loaded yet, need to load, describe and unload again,
        # thus avoiding passing big files to BT server:
        flush_data = False
        try:
            assert not self.data.empty
            pass

        except (AssertionError, AttributeError) as e:
            self.read_csv()
            flush_data = True

        self.data_stat = self.data.describe()
        self.log.info('Data summary:\n{}'.format(self.data_stat.to_string()))

        if flush_data:
            self.data = None
            self.log.info('Flushed data.')

        return self.data_stat

    def to_btfeed(self):
        """
        Performs BTgymData-->bt.feed conversion.

        Returns:
             bt.datafeed instance.
        """
        try:
            assert not self.data.empty
            btfeed = btfeeds.PandasDirectData(
                dataname=self.data,
                timeframe=self.timeframe,
                datetime=self.datetime,
                open=self.open,
                high=self.high,
                low=self.low,
                close=self.close,
                volume=self.volume,
                openinterest=self.openinterest
            )
            btfeed.numrecords = self.data.shape[0]
            return btfeed

        except (AssertionError, AttributeError) as e:
            msg = 'Instance holds no data. Hint: forgot to call .read_csv()?'
            self.log.error(msg)
            raise AssertionError(msg)

    def sample(self, **kwargs):
        return self._sample(**kwargs)

    def _sample(self, get_new=True, sample_type=0, b_alpha=1.0, b_beta=1.0):
        """
        Samples continuous subset of data.

        Args:
            get_new (bool):                 sample new (True) or reuse (False) last made sample;
            sample_type (int or bool):      0 (train) or 1 (test) - get sample from train or test data subsets
                                            respectively.
            b_alpha (float):                beta-distribution sampling alpha > 0, valid for train episodes.
            b_beta (float):                 beta-distribution sampling beta > 0, valid for train episodes.

        Returns:
        if no sample_class_ref param been set:
            BTgymDataset instance with number of records ~ max_episode_len,
            where `~` tolerance is set by `time_gap` param;
        else:
            `sample_class_ref` instance with same as above number of records.

        Note:
                Train sample start position within interval is drawn from beta-distribution
                with default parameters b_alpha=1, b_beta=1, i.e. uniform one.
                Beta-distribution makes skewed sampling possible , e.g.
                to give recent episodes higher probability of being sampled, e.g.:  b_alpha=10, b_beta=0.8.
                Test samples are always uniform one.

        """
        try:
            assert self.is_ready

        except AssertionError:
            self.log.exception(
                'Sampling attempt: data not ready. Hint: forgot to call data.reset()?'
            )
            raise AssertionError

        try:
            assert sample_type in [0, 1]

        except AssertionError:
            self.log.exception(
                'Sampling attempt: expected sample type be in {}, got: {}'.\
                format([0, 1], sample_type)
            )
            raise AssertionError

        if self.sample_instance is None or get_new:
            if sample_type == 0:
                # Get beta_distributed sample in train interval:
                self.sample_instance = self._sample_interval(
                    self.train_interval,
                    b_alpha=b_alpha,
                    b_beta=b_beta,
                    name='train_' + self.sample_name
                )

            else:
                # Get uniform sample in test interval:
                self.sample_instance = self._sample_interval(
                    self.test_interval,
                    b_alpha=1,
                    b_beta=1,
                    name='test_' + self.sample_name
                )
            self.sample_instance.metadata['type'] = sample_type
            self.sample_instance.metadata['sample_num'] = self.sample_num
            self.sample_instance.metadata['parent_sample_num'] = copy.deepcopy(self.metadata['sample_num'])
            self.sample_instance.metadata['parent_sample_type'] = copy.deepcopy(self.metadata['type'])
            self.sample_num += 1

        else:
            # Do nothing:
            self.log.debug('Reusing sample, id: {}'.format(self.sample_instance.filename))

        return self.sample_instance

    def _sample_random(self, name='random_sample_'):
        """
        Randomly samples continuous subset of data.

        Args:
            name:        str, sample filename id

        Returns:
             BTgymDataset instance with number of records ~ max_episode_len,
             where `~` tolerance is set by `time_gap` param.
        """
        try:
            assert not self.data.empty

        except (AssertionError, AttributeError) as e:
            self.log.exception('Instance holds no data. Hint: forgot to call .read_csv()?')
            raise AssertionError

        self.log.debug('Maximum sample time duration set to: {}.'.format(self.max_sample_len_delta))
        self.log.debug('Respective number of steps: {}.'.format(self.sample_num_records))
        self.log.debug('Maximum allowed data time gap set to: {}.\n'.format(self.max_time_gap))

        # Sanity check param:
        max_attempts = 100
        attempts = 0

        # # Keep sampling random enter points until all conditions are met:
        while attempts <= max_attempts:

            # Randomly sample record (row) from entire datafeed:
            first_row = int((self.data.shape[0] - self.sample_num_records - 1) * random.random())
            sample_first_day = self.data[first_row:first_row + 1].index[0]
            self.log.debug('Sample start: {}, weekday: {}.'.format(sample_first_day, sample_first_day.weekday()))

            # Keep sampling until good day:
            while not sample_first_day.weekday() in self.start_weekdays and attempts <= max_attempts:
                self.log.debug('Not a good day to start, resampling...')
                first_row = int((self.data.shape[0] - self.sample_num_records - 1) * random.random())
                sample_first_day = self.data[first_row:first_row + 1].index[0]
                self.log.debug('Sample start: {}, weekday: {}.'.format(sample_first_day, sample_first_day.weekday()))
                attempts +=1

            # Check if managed to get proper weekday:
            assert attempts <= max_attempts, \
                'Quitting after {} sampling attempts. Hint: check sampling params / dataset consistency.'. \
                format(attempts)

            # If 00 option set, get index of first record of that day:
            if self.start_00:
                adj_timedate = sample_first_day.date()
                self.log.debug('Start time adjusted to <00:00>')

            else:
                adj_timedate = sample_first_day

            first_row = self.data.index.get_loc(adj_timedate, method='nearest')

            # Easy part:
            last_row = first_row + self.sample_num_records  # + 1
            sampled_data = self.data[first_row: last_row]
            sample_len = (sampled_data.index[-1] - sampled_data.index[0]).to_pytimedelta()
            self.log.debug('Actual sample duration: {}.'.format(sample_len, ))
            self.log.debug('Total episode time gap: {}.'.format(sample_len - self.max_sample_len_delta))

            # Perform data gap check:
            if sample_len - self.max_sample_len_delta < self.max_time_gap:
                self.log.debug('Sample accepted.')
                # If sample OK - compose and return sample:
                new_instance = self.nested_class_ref(**self.nested_params)
                new_instance.filename = name + 'n{}_at_{}'.format(self.sample_num, adj_timedate)
                self.log.info('Sample id: <{}>.'.format(new_instance.filename))
                new_instance.data = sampled_data
                new_instance.metadata['type'] = 'random_sample'
                new_instance.metadata['first_row'] = first_row

                return new_instance

            else:
                self.log.debug('Duration too big, resampling...\n')
                attempts += 1

        # Got here -> sanity check failed:
        msg = ('Quitting after {} sampling attempts.' +
               'Hint: check sampling params / dataset consistency.').format(attempts)
        self.log.error(msg)
        raise RuntimeError(msg)

    def _sample_interval(self, interval, b_alpha=1.0, b_beta=1.0, name='interval_sample_'):
        """
        Samples continuous subset of data,
        such as entire episode records lie within positions specified by interval.
        Episode start position within interval is drawn from beta-distribution parametrised by `b_alpha, b_beta`.
        By default distribution is uniform one.

        Args:
            interval:       tuple, list or 1d-array of integers of length 2: [lower_row_number, upper_row_number];
            b_alpha:        float > 0, sampling B-distribution alpha param, def=1;
            b_beta:         float > 0, sampling B-distribution beta param, def=1;
            name:           str, sample filename id


        Returns:
             - BTgymDataset instance such as:
                1. number of records ~ max_episode_len, subj. to `time_gap` param;
                2. actual episode start position is sampled from `interval`;
             - `False` if it is not possible to sample instance with set args.
        """
        try:
            assert not self.data.empty

        except (AssertionError, AttributeError) as e:
            self.log.exception('Instance holds no data. Hint: forgot to call .read_csv()?')
            raise  AssertionError

        try:
            assert len(interval) == 2

        except AssertionError:
            self.log.exception(
                'Invalid interval arg: expected list or tuple of size 2, got: {}'.format(interval)
            )
            raise AssertionError

        try:
            assert b_alpha > 0 and b_beta > 0

        except AssertionError:
            self.log.exception(
                'Expected positive B-distribution [alpha, beta] params, got: {}'.format([b_alpha, b_beta])
            )
            raise AssertionError

        sample_num_records = self.sample_num_records

        try:
            assert interval[0] < interval[-1] <= self.data.shape[0]

        except AssertionError:
            self.log.exception(
                'Cannot sample with size {}, inside {} from dataset of {} records'.
                 format(sample_num_records, interval, self.data.shape[0])
            )
            raise AssertionError

        self.log.debug('Maximum sample time duration set to: {}.'.format(self.max_sample_len_delta))
        self.log.debug('Respective number of steps: {}.'.format(sample_num_records))
        self.log.debug('Maximum allowed data time gap set to: {}.\n'.format(self.max_time_gap))

        # Sanity check param:
        max_attempts = 100
        attempts = 0

        # # Keep sampling random enter points until all conditions are met:
        while attempts <= max_attempts:

            first_row = interval[0] + int(
                (interval[-1] - interval[0] - sample_num_records) * random_beta(a=b_alpha, b=b_beta)
            )

            #print('_sample_interval_sample_num_records: ', sample_num_records)
            #print('_sample_interval_first_row: ', first_row)

            sample_first_day = self.data[first_row:first_row + 1].index[0]
            self.log.debug('Sample start: {}, weekday: {}.'.format(sample_first_day, sample_first_day.weekday()))

            # Keep sampling until good day:
            while not sample_first_day.weekday() in self.start_weekdays and attempts <= max_attempts:
                self.log.debug('Not a good day to start, resampling...')
                first_row = interval[0] + round(
                    (interval[-1] - interval[0] - sample_num_records) * random_beta(a=b_alpha, b=b_beta)
                )
                #print('r_sample_interval_sample_num_records: ', sample_num_records)
                #print('r_sample_interval_first_row: ', first_row)
                sample_first_day = self.data[first_row:first_row + 1].index[0]
                self.log.debug('Sample start: {}, weekday: {}.'.format(sample_first_day, sample_first_day.weekday()))
                attempts += 1

            # Check if managed to get proper weekday:
            try:
                assert attempts <= max_attempts

            except AssertionError:
                self.log.exception(
                    'Quitting after {} sampling attempts. Hint: check sampling params / dataset consistency.'.
                    format(attempts)
                )
                raise RuntimeError

            # If 00 option set, get index of first record of that day:
            if self.start_00:
                adj_timedate = sample_first_day.date()
                self.log.debug('Start time adjusted to <00:00>')

            else:
                adj_timedate = sample_first_day

            first_row = self.data.index.get_loc(adj_timedate, method='nearest')

            # Easy part:
            last_row = first_row + sample_num_records  # + 1
            sampled_data = self.data[first_row: last_row]
            sample_len = (sampled_data.index[-1] - sampled_data.index[0]).to_pytimedelta()
            self.log.debug('Actual sample duration: {}.'.format(sample_len))
            self.log.debug('Total sample time gap: {}.'.format(sample_len - self.max_sample_len_delta))

            # Perform data gap check:
            if sample_len - self.max_sample_len_delta < self.max_time_gap:
                self.log.debug('Sample accepted.')
                # If sample OK - return new dataset:
                new_instance = self.nested_class_ref(**self.nested_params)
                new_instance.filename = name + 'num_{}_at_{}'.format(self.sample_num, adj_timedate)
                self.log.info('New sample id: <{}>.'.format(new_instance.filename))
                new_instance.data = sampled_data
                new_instance.metadata['type'] = 'interval_sample'
                new_instance.metadata['first_row'] = first_row

                return new_instance

            else:
                self.log.debug('Attempt {}: duration too big, resampling, ...\n'.format(attempts))
                attempts += 1

        # Got here -> sanity check failed:
        msg = ('Quitting after {} sampling attempts.' +
               'Hint: check sampling params / dataset consistency.').format(attempts)
        self.log.error(msg)
        raise RuntimeError(msg)




